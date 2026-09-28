"""车载设备接口：维护车载ATP，覆盖登记异常、排查通信、办理停用、排查批量提交等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    EntryPayload,
    InspectionPayload,
    InspectionResult,
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
    train: str | None = Query(default=None, description="按所属列车检索"),
    status: str | None = Query(default=None, description="正常、自检异常、通信中断、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按设备编号、所属列车与车载状态过滤车载设备列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"车载状态仅支持：{'、'.join(STATUSES)}")
    items, total = service.list_entries(keyword=keyword, train=train, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 注意：静态路径必须排在 /{entry_id} 之前，否则「export」会被当成设备id。
@router.get("/export")
def export_entries(train: str | None = None) -> dict[str, Any]:
    """导出车载设备清单：可按所属列车限定，返回全量数据。"""
    items, total = service.list_entries(train=train, page=1, size=10000)
    return {"module": "atp", "total": total, "items": items}


@router.post("/inspections", response_model=InspectionResult)
def submit_inspection(payload: InspectionPayload) -> InspectionResult:
    """同列车多台车载设备的排查结果一次提交。

    逐台落库、缺项保留原数据、同票据/同批次只生效一次、部分失败不回滚成功项。
    """
    if not payload.items:
        raise HTTPException(status_code=400, detail="本次提交没有任何设备的排查结果")
    items = [item.model_dump() for item in payload.items]
    result = service.submit_inspection(items, submission_id=payload.submission_id)
    return InspectionResult(**result)


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


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条车载ATP执行登记异常、排查通信、办理停用；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
