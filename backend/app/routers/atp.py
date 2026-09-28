"""车载设备接口：维护车载ATP，覆盖登记异常、排查通信、办理停用与批量排查提交。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BatchActionResult,
    BatchInspectionPayload,
    DeviceResult,
    EntryPayload,
    InspectionItemPayload,
    PageResult,
)
from app.services.atp import AtpService

router = APIRouter(prefix="/api/atp", tags=["车载设备"])

service = AtpService()

LIST_FIELDS = ["设备编号", "所属列车", "设备型号", "软件版本", "自检状态", "制动接口", "记录数据", "车载状态"]
STATUSES = ["正常", "自检异常", "通信中断", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按设备编号检索"),
    status: str | None = Query(default=None, description="正常、自检异常、通信中断、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按设备编号与状态过滤车载设备列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条车载ATP明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"车载ATP {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条车载ATP，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="车载ATP已登记", entry=entry)


@router.post("/inspection", response_model=BatchActionResult)
def submit_inspection(payload: BatchInspectionPayload) -> BatchActionResult:
    """提交同一列车多台设备的排查结果。

    逐台落库并逐台返回：缺检查结论的设备保留原值并说明，重复设备只生效一次，
    个别设备失败不回滚同批已成功的设备。
    """
    items = [item.model_dump() for item in payload.items]
    ok, message, results = service.submit_inspection(items)
    return BatchActionResult(
        ok=ok,
        message=message,
        success_count=sum(1 for r in results if r["ok"] and not r["skipped"]),
        failure_count=sum(1 for r in results if not r["ok"]),
        skipped_count=sum(1 for r in results if r["skipped"]),
        results=[DeviceResult(**r) for r in results],
    )


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条车载ATP执行登记异常、排查通信、办理停用；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出车载设备清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "atp", "total": total, "items": items}
