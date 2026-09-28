"""车载设备业务规则：状态流转、批量排查、字段校验与筛选口径都收在这里。

排查提交有两个必须守住的口径：
1. 每台设备都按自己的 id/编号定位并更新各自的记录，不共享任何中间变量；
2. 列表与详情共用 present() 派生车载状态，避免同一条记录给出两套结论。
"""
from __future__ import annotations

import threading
from typing import Any

from app.store import store

MODULE = "atp"
REQUIRED_FIELDS = ["设备编号", "所属列车", "设备型号"]
STATUS_ORDER = ["正常", "自检异常", "通信中断", "已停用"]
ACTION_RULES = {"登记异常": "自检异常", "排查通信": "通信中断", "办理停用": "已停用"}
NEGATIVE_ACTIONS = []

# 排查时可提交的检查结论，非法值只让对应那台设备失败，不波及同批次其他设备。
SELF_CHECK_RESULTS = ["正常", "自检异常"]
BRAKE_RESULTS = ["正常", "制动异常"]
DISABLED_STATUS = "已停用"


class AtpService:
    def __init__(self) -> None:
        # 批量提交串行落库，避免两次请求同时写同一份列表造成后写覆盖先写。
        self._submit_lock = threading.Lock()

    # ---- 读取口径（列表与详情共用，保证车载状态只有一套结论）----

    def _derive_status(self, entry: dict[str, Any]) -> str:
        """根据停用/通信标记和检查字段派生唯一车载状态。"""
        if entry.get("_停用"):
            return "已停用"
        if entry.get("_通信中断"):
            return "通信中断"
        if entry.get("制动接口") == "制动异常" or entry.get("自检状态") == "自检异常":
            return "自检异常"
        return "正常"

    def present(self, entry: dict[str, Any]) -> dict[str, Any]:
        """把存储记录转成对外字段；车载状态始终由当前数据现算，不读旧结论。"""
        item = dict(entry)
        item.pop("_停用", None)
        item.pop("_通信中断", None)
        item["status"] = self._derive_status(entry)
        item["车载状态"] = item["status"]
        return item

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("设备编号", ""))]
        if status:
            rows = [row for row in rows if self._derive_status(row) == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self.present(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self.present(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with self._submit_lock:
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            # 新登记设备尚无检查结论，派生状态为“正常”。
            entry["自检状态"] = None
            entry["制动接口"] = None
            entry["记录数据"] = None
            entry["_停用"] = False
            entry["_通信中断"] = False
            entry["pending"] = True
            entry["abnormal"] = False
            rows.append(entry)
            return self.present(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"车载ATP {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于车载设备可执行范围"
        target = ACTION_RULES[action]
        with self._submit_lock:
            if target == "已停用":
                entry["_停用"] = True
            elif target == "通信中断":
                entry["_通信中断"] = True
            elif target == "自检异常":
                entry["自检状态"] = "自检异常"
            entry["pending"] = target != DISABLED_STATUS
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return self.present(entry), f"车载ATP已{action}"

    # ---- 批量排查提交 ----

    def submit_inspection(
        self, items: list[dict[str, Any]]
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        """逐台处理一次排查提交。

        每台设备独立成功/失败：缺检查结论时保留该台原有数据并给出说明；
        同批次重复出现的同一台设备只生效一次；任何一台失败都不回滚已成功的设备。
        """
        results: list[dict[str, Any]] = []
        if not items:
            return False, "本次提交没有包含任何设备排查结果", results

        with self._submit_lock:
            seen: set[int] = set()
            for raw in items:
                item = raw or {}
                code = str(item.get("设备编号") or "").strip()
                entry_id = item.get("id")
                if entry_id is None and not code:
                    results.append({
                        "id": None,
                        "设备编号": None,
                        "ok": False,
                        "skipped": False,
                        "message": "未提供设备 id 或设备编号，无法定位记录",
                        "entry": None,
                    })
                    continue
                entry: dict[str, Any] | None = None
                if entry_id is not None:
                    entry = store.find(MODULE, int(entry_id))
                else:
                    for row in store.rows(MODULE):
                        if str(row.get("设备编号") or "").strip() == code:
                            entry = row
                            break
                if entry is None:
                    label = f"车载ATP {entry_id}" if entry_id is not None else f"设备编号 {code}"
                    results.append({
                        "id": entry_id,
                        "设备编号": code or None,
                        "ok": False,
                        "skipped": False,
                        "message": f"{label} 不存在或已归档",
                        "entry": None,
                    })
                    continue
                row_id = int(entry["id"])
                if row_id in seen:
                    results.append({
                        "id": row_id,
                        "设备编号": entry.get("设备编号"),
                        "ok": True,
                        "skipped": True,
                        "message": "该设备在本次提交中重复出现，只生效第一次提交",
                        "entry": self.present(entry),
                    })
                    continue
                seen.add(row_id)

                device_label = str(entry.get("设备编号") or f"id={row_id}")
                try:
                    warnings: list[str] = []
                    new_self_check = str(item.get("自检状态") or "").strip()
                    new_brake = str(item.get("制动接口") or "").strip()
                    new_record = item.get("记录数据")

                    # 通信/停用标记代表的是更早流程里的事实，排查复查不静默清除。
                    if new_self_check:
                        if new_self_check not in SELF_CHECK_RESULTS:
                            raise ValueError(
                                f"自检状态「{new_self_check}」不合法，"
                                f"允许值：{'、'.join(SELF_CHECK_RESULTS)}"
                            )
                        entry["自检状态"] = new_self_check
                    else:
                        warnings.append("未提交自检状态，保留原有值")
                    if new_brake:
                        if new_brake not in BRAKE_RESULTS:
                            raise ValueError(
                                f"制动接口结论「{new_brake}」不合法，"
                                f"允许值：{'、'.join(BRAKE_RESULTS)}"
                            )
                        entry["制动接口"] = new_brake
                    else:
                        warnings.append("制动接口检查结果缺失，保留原有值")
                    if new_record is not None and str(new_record).strip():
                        entry["记录数据"] = str(new_record).strip()

                    entry["abnormal"] = self._derive_status(entry) == "自检异常"
                    entry["pending"] = self._derive_status(entry) != DISABLED_STATUS
                    message = f"{device_label} 排查结果已落库"
                    if warnings:
                        message += "；" + "，".join(warnings)
                    results.append({
                        "id": entry["id"],
                        "设备编号": entry.get("设备编号"),
                        "ok": True,
                        "skipped": False,
                        "message": message,
                        "entry": self.present(entry),
                    })
                except ValueError as exc:
                    # 校验失败前没有写入该台任何字段，已成功的其他设备不受影响。
                    results.append({
                        "id": entry.get("id"),
                        "设备编号": entry.get("设备编号"),
                        "ok": False,
                        "skipped": False,
                        "message": f"{device_label} 提交失败：{exc}，原有数据已保留",
                        "entry": None,
                    })
                except Exception as exc:  # pragma: no cover - 防御未知写入异常
                    results.append({
                        "id": entry.get("id"),
                        "设备编号": entry.get("设备编号"),
                        "ok": False,
                        "skipped": False,
                        "message": f"{device_label} 提交失败：{exc}，原有数据已保留",
                        "entry": None,
                    })

        success = sum(1 for r in results if r["ok"] and not r["skipped"])
        skipped = sum(1 for r in results if r["skipped"])
        failure = sum(1 for r in results if not r["ok"])
        summary = f"排查提交完成：{success} 台生效"
        if skipped:
            summary += f"，{skipped} 台重复跳过"
        if failure:
            failed_labels = "、".join(
                str(r.get("设备编号") or r.get("id")) for r in results if not r["ok"]
            )
            summary += f"，{failure} 台失败（{failed_labels}），失败设备未影响其余设备"
        return failure == 0, summary, results
