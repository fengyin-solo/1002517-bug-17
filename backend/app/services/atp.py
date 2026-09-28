"""车载设备业务规则：状态流转、字段校验、排查批量提交与筛选口径都收在这里。

排查提交（POST /api/atp/inspections）的关键约束：

1. 一次提交里的每台设备各自落到自己的记录上，绝不共享中间变量；
2. 单台设备处理失败不影响批次里已经/后续处理成功的设备（部分成功，不做整批回滚）；
3. 检查结果缺项时保留该设备原有数据，只在返回信息里点名缺项的设备；
4. 同一个提交批次（同一提交票据、或批次内重复出现同一台设备）只生效一次；
5. 列表与详情的车载状态统一来自「车载状态」字段，英文 status 只作为镜像，
   避免同一台设备在两处给出不一致的结论。
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from app.store import store

MODULE = "atp"
REQUIRED_FIELDS = ["设备编号", "所属列车", "设备型号"]
STATUS_ORDER = ["正常", "自检异常", "通信中断", "已停用"]
ACTION_RULES = {"登记异常": "自检异常", "排查通信": "通信中断", "办理停用": "已停用"}
NEGATIVE_ACTIONS = []

# 排查提交允许更新的字段：自检状态、制动接口；其余字段不接受排查提交改动。
INSPECTION_FIELDS = ["自检状态", "制动接口"]
SELF_CHECK_VALUES = {"正常": "正常", "异常": "自检异常"}
BRAKE_VALUES = {"正常": "正常", "异常": "异常"}

# 已处理过的提交票据 -> 首次处理结果，用于重复提交只生效一次。
_processed_submissions: dict[str, dict[str, Any]] = {}
_submission_lock = threading.Lock()


def _clean(value: Any) -> str:
    """把前端传来的空串/空白统一成空值口径。"""
    return str(value if value is not None else "").strip()


def sync_status(entry: dict[str, Any]) -> None:
    """车载状态以「车载状态」字段为唯一口径，同步英文镜像与待处理标记。

    列表筛选、概览统计走英文 status，页面展示走中文「车载状态」，
    两者必须同源，否则会出现列表和详情两套结论。
    """
    display = _clean(entry.get("车载状态"))
    if display not in STATUS_ORDER:
        display = _clean(entry.get("status")) if _clean(entry.get("status")) in STATUS_ORDER else STATUS_ORDER[0]
        entry["车载状态"] = display
    entry["status"] = display
    entry["pending"] = display != STATUS_ORDER[-1]
    entry["abnormal"] = display not in ("正常", "已停用")


class AtpService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        train: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._normalized(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("设备编号", ""))]
        if train:
            rows = [row for row in rows if train in str(row.get("所属列车", ""))]
        if status:
            rows = [row for row in rows if row.get("车载状态") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._normalized(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _clean(values.get(field))]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: _clean(values.get(field)) for field in REQUIRED_FIELDS})
        software_version = _clean(values.get("软件版本"))
        if software_version:
            entry["软件版本"] = software_version
        entry["自检状态"] = "未排查"
        entry["制动接口"] = "未排查"
        entry["记录数据"] = ""
        entry["车载状态"] = STATUS_ORDER[0]
        sync_status(entry)
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"车载ATP {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于车载设备可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["车载状态"] = target
        sync_status(entry)
        return self._normalized(entry), f"车载ATP已{action}"

    # ------------------------------------------------------------------
    # 排查批量提交
    # ------------------------------------------------------------------
    def submit_inspection(
        self,
        items: list[dict[str, Any]],
        *,
        submission_id: str | None = None,
    ) -> dict[str, Any]:
        """按设备逐台落排查结果，返回逐台结果与汇总，部分成功不回滚。"""
        token = _clean(submission_id) or uuid.uuid4().hex

        # 同一提交票据重复提交：直接回放首次结论，不再写任何数据。
        with _submission_lock:
            cached = _processed_submissions.get(token)
        if cached is not None:
            replay = dict(cached)
            replay["replayed"] = True
            replay["message"] = f"该排查提交已处理过（提交票据 {token}），本次未重复生效"
            return replay

        results: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        success_count = failure_count = skipped_count = 0

        for raw in items:
            outcome = self._apply_one_inspection(raw, seen_ids)
            results.append(outcome)
            if outcome["result"] == "success":
                success_count += 1
            elif outcome["result"] == "skipped":
                skipped_count += 1
            else:
                failure_count += 1

        summary = self._build_summary(success_count, failure_count, skipped_count)
        payload: dict[str, Any] = {
            "ok": failure_count == 0,
            "message": summary,
            "submission_id": token,
            "replayed": False,
            "success_count": success_count,
            "failure_count": failure_count,
            "skipped_count": skipped_count,
            "items": results,
        }
        # 只有确实处理过设备（或整批都是可识别的重复项）才登记票据，
        # 纯失败批次允许前端修正后用新票据重提。
        if success_count > 0 or (items and failure_count == 0):
            with _submission_lock:
                _processed_submissions.setdefault(token, payload)
        return payload

    def _apply_one_inspection(
        self, raw: dict[str, Any], seen_ids: set[int]
    ) -> dict[str, Any]:
        """处理批次中的单台设备；任何异常都收敛成该设备的失败结论，不抛出。"""
        label = f"设备 {_clean(raw.get('设备编号')) or raw.get('设备id') or '未知'}"
        entry_id_raw = raw.get("设备id", raw.get("id"))
        try:
            entry_id = int(entry_id_raw)
        except (TypeError, ValueError):
            return {"result": "failed", "设备": label, "message": "缺少有效的设备id，未写入"}

        label = f"设备 {_clean(raw.get('设备编号')) or entry_id}"

        if entry_id in seen_ids:
            return {
                "result": "skipped",
                "设备id": entry_id,
                "设备": label,
                "message": "同一批次内重复出现，仅第一条生效，本条已跳过",
            }

        entry = store.find(MODULE, entry_id)
        if entry is None:
            return {
                "result": "failed",
                "设备id": entry_id,
                "设备": label,
                "message": f"车载ATP {entry_id} 不存在或已归档，未写入",
            }

        # 批次里声明的所属列车与记录不一致时点名下线/串车，避免改到别的列车设备。
        train = _clean(raw.get("所属列车"))
        if train and train != _clean(entry.get("所属列车")):
            return {
                "result": "failed",
                "设备id": entry_id,
                "设备": label,
                "message": (
                    f"所属列车不匹配：提交为「{train}」，记录为"
                    f"「{_clean(entry.get('所属列车'))}」，未写入"
                ),
            }

        seen_ids.add(entry_id)

        device_code = _clean(entry.get("设备编号")) or str(entry_id)
        missing = [field for field in INSPECTION_FIELDS if not _clean(raw.get(field))]
        provided = [field for field in INSPECTION_FIELDS if _clean(raw.get(field))]
        if not provided:
            # 一项检查结果都没交：整台不动，保留原有数据并点名是哪台缺。
            kept = "、".join(f"{f}={_clean(entry.get(f)) or '空'}" for f in missing)
            return {
                "result": "skipped",
                "设备id": entry_id,
                "设备": f"设备 {device_code}",
                "message": f"{device_code}：缺少{'、'.join(missing)}检查结果，保留原有数据（{kept}），未更新",
                "missing": missing,
            }

        # 先校验本次提供的取值，非法则整台不写，避免半合法数据落库。
        try:
            self._validate_inspection(raw, provided)
        except ValueError as exc:
            return {
                "result": "failed",
                "设备id": entry_id,
                "设备": f"设备 {device_code}",
                "message": f"{device_code}：{exc}，原有数据已保留，未更新",
            }

        self._write_inspection(entry, raw, provided)
        detail = f"自检状态={entry['自检状态']}，制动接口={entry['制动接口']}"
        message = f"{device_code}：排查结果已记录（{detail}），车载状态={entry['车载状态']}"
        if missing:
            # 字段级合并：交了哪项写哪项，没交的项保留原值并说明。
            kept = "、".join(f"{f}={_clean(entry.get(f)) or '空'}" for f in missing)
            message = f"{device_code}：已记录{'、'.join(provided)}；缺少{'、'.join(missing)}检查结果，保留原值（{kept}）"
        return {
            "result": "success",
            "设备id": entry_id,
            "设备": f"设备 {device_code}",
            "message": message,
            "missing": missing,
            "entry": self._normalized(entry),
        }

    def _validate_inspection(self, raw: dict[str, Any], provided: list[str]) -> None:
        if "自检状态" in provided and _clean(raw.get("自检状态")) not in SELF_CHECK_VALUES:
            raise ValueError(f"自检状态「{_clean(raw.get('自检状态'))}」取值非法")
        if "制动接口" in provided and _clean(raw.get("制动接口")) not in BRAKE_VALUES:
            raise ValueError(f"制动接口「{_clean(raw.get('制动接口'))}」取值非法")

    def _write_inspection(self, entry: dict[str, Any], raw: dict[str, Any], provided: list[str]) -> None:
        """把单台设备本次提交的排查结果写到它自己的记录上，缺交的字段不动。"""
        if "自检状态" in provided:
            entry["自检状态"] = SELF_CHECK_VALUES[_clean(raw.get("自检状态"))]
        if "制动接口" in provided:
            entry["制动接口"] = _clean(raw.get("制动接口"))
        # 车载状态由自检结果唯一派生：自检正常即车载正常，自检异常即自检异常。
        # 人工停用状态不由排查接口回改，避免刷新后两套结论互相打架。
        if entry.get("车载状态") != "已停用":
            entry["车载状态"] = "自检异常" if entry["自检状态"] == "自检异常" else "正常"
        remark = _clean(raw.get("记录数据"))
        if remark:
            entry["记录数据"] = remark
        sync_status(entry)

    def _build_summary(self, success: int, failure: int, skipped: int) -> str:
        parts = [f"成功 {success} 台"]
        if skipped:
            parts.append(f"跳过 {skipped} 台（缺项或重复，保留原数据）")
        if failure:
            parts.append(f"失败 {failure} 台（未影响其余设备）")
        return "排查提交完成：" + "，".join(parts)

    def _normalized(self, entry: dict[str, Any]) -> dict[str, Any]:
        """读时归一：补齐排查字段并同步状态口径，保证列表/详情一致。

        直接在原记录上修复（例如种子数据里中文车载状态与英文镜像不一致），
        让后续任何读取都只有一套结论。
        """
        entry.setdefault("自检状态", "未排查")
        entry.setdefault("制动接口", "未排查")
        entry.setdefault("记录数据", "")
        sync_status(entry)
        return entry
