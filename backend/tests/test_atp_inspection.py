"""车载ATP批量排查提交的行为测试。

直接运行：backend/.venv/bin/python tests/test_atp_inspection.py
不依赖 pytest/httpx，在服务层和路由函数层逐场景校验。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.routers import atp as atp_router  # noqa: E402
from app.schemas import BatchInspectionPayload, EntryPayload  # noqa: E402
from app.services.atp import AtpService  # noqa: E402
from app.store import store  # noqa: E402

service: AtpService = atp_router.service
passed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if not condition:
        raise AssertionError(f"用例失败：{name} {detail}")
    passed += 1
    print(f"  ✓ {name}")


def snapshot():
    return copy.deepcopy(store.rows("atp"))


def restore(rows) -> None:
    store.rows("atp").clear()
    store.rows("atp").extend(copy.deepcopy(rows))


def submit(items):
    return atp_router.submit_inspection(
        BatchInspectionPayload.model_validate({"items": items})
    )


def main() -> None:
    original = snapshot()
    try:
        # 场景一：同一列车一次提交多台，每台都落到自己的记录上
        rows = snapshot()
        restore(rows)
        result = submit([
            {"id": 1, "自检状态": "正常", "制动接口": "正常", "记录数据": "排查A"},
            {"id": 2, "自检状态": "正常", "制动接口": "正常", "记录数据": "排查B"},
        ])
        check("多台提交整体成功", result.ok, result.message)
        check("生效两台", result.success_count == 2)
        r1 = service.get_entry(1)
        r2 = service.get_entry(2)
        check("第一台写自己的记录", r1["自检状态"] == "正常" and r1["记录数据"] == "排查A")
        check("第二台写自己的记录", r2["自检状态"] == "正常" and r2["记录数据"] == "排查B")
        check("两台的制动接口都保留了提交值",
              r1["制动接口"] == "正常" and r2["制动接口"] == "正常")

        # 场景二：刷新后（重新读列表/详情）不出现两套结论
        items, _ = service.list_entries(page=1, size=100)
        list_map = {row["id"]: row for row in items}
        for entry_id in (1, 2):
            detail = service.get_entry(entry_id)
            check(f"id={entry_id} 列表与详情车载状态一致",
                  list_map[entry_id]["车载状态"] == detail["车载状态"] == detail["status"])
        # 第二台从自检异常恢复为正常后，两个字段都必须是新结论
        check("复查通过后旧结论不再残留",
              list_map[2]["车载状态"] == "正常" and list_map[2]["自检状态"] == "正常")

        # 场景三：制动接口结果缺失 → 保留原值并说明是哪台设备缺
        restore(rows)
        result = submit([
            {"id": 1, "自检状态": "自检异常", "记录数据": "排查C"},
        ])
        check("缺制动接口时整体仍按成功处理", result.ok and result.success_count == 1)
        msg = result.results[0].message
        check("提示里点名设备并说明制动接口缺失、保留原值",
              "ATP-0001" in msg and "制动接口检查结果缺失" in msg and "保留原有值" in msg, msg)
        r1 = service.get_entry(1)
        check("原制动接口值未被清空", r1["制动接口"] == "正常")
        check("提交了的自检状态正常更新", r1["自检状态"] == "自检异常")

        # 场景四：同批次重复提交同一台只生效一次
        restore(rows)
        result = submit([
            {"id": 1, "自检状态": "正常", "制动接口": "正常"},
            {"id": 1, "自检状态": "自检异常", "制动接口": "制动异常"},
        ])
        check("重复提交不报错", result.ok)
        check("一台生效、一台跳过", result.success_count == 1 and result.skipped_count == 1)
        check("跳过的是第二次提交", result.results[1].skipped)
        r1 = service.get_entry(1)
        check("保留第一次提交的结论",
              r1["自检状态"] == "正常" and r1["制动接口"] == "正常")

        # 场景五：一台失败不回滚已成功的其他设备
        restore(rows)
        result = submit([
            {"id": 1, "自检状态": "正常", "制动接口": "正常", "记录数据": "已成功"},
            {"id": 2, "自检状态": "胡编的值", "制动接口": "正常"},
            {"id": 999, "自检状态": "正常", "制动接口": "正常"},
        ])
        check("存在失败设备时整体 ok=False", not result.ok)
        check("成功一台、失败两台", result.success_count == 1 and result.failure_count == 2)
        check("失败说明点名具体设备", "ATP-0002" in result.results[1].message)
        r1 = service.get_entry(1)
        r2 = service.get_entry(2)
        check("成功设备的结果依然在", r1["记录数据"] == "已成功" and r1["自检状态"] == "正常")
        check("失败设备原值未被改动", r2["自检状态"] == "自检异常" and r2["制动接口"] == "正常")

        # 附加：空提交与按设备编号定位
        restore(rows)
        result = submit([])
        check("空批次被拦下", not result.ok)
        result = submit([
            {"设备编号": "ATP-0002", "自检状态": "正常", "制动接口": "制动异常"},
        ])
        check("支持按设备编号定位落库", result.ok and result.success_count == 1)
        r2 = service.get_entry(2)
        check("编号定位写入了正确记录",
              r2["自检状态"] == "正常" and r2["制动接口"] == "制动异常"
              and r2["车载状态"] == "自检异常")

        # 附加：单台动作接口仍可流转，且列表/详情口径一致
        restore(rows)
        action = atp_router.run_action(2, EntryPayload.model_validate({"values": {"action": "办理停用"}}))
        check("停用动作生效", action.ok and action.entry["车载状态"] == "已停用")
        items, _ = service.list_entries(page=1, size=100)
        check("停用后列表详情一致",
              next(i for i in items if i["id"] == 2)["车载状态"]
              == service.get_entry(2)["车载状态"] == "已停用")
    finally:
        restore(original)

    print(f"\n全部 {passed} 项断言通过")


if __name__ == "__main__":
    main()
