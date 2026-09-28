"""车载ATP批量排查提交的行为测试（服务层，不依赖 fastapi/pydantic）。

直接运行：python3 tests/test_atp_inspection_lite.py
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 阻止 app/__init__ 链路引入缺失的三方依赖：直接按文件加载 store 与 service。
import importlib.util  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "app"


def load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


seed_mod = load("seed_mod", "seed.py")
import types  # noqa: E402

store_mod = types.ModuleType("app.store")
store_mod.SEED_ROWS = seed_mod.SEED_ROWS

class Store:
    def __init__(self) -> None:
        self._tables = {
            name: [dict(row) for row in rows]
            for name, rows in seed_mod.SEED_ROWS.items()
        }

    def rows(self, module):
        return self._tables.setdefault(module, [])

    def find(self, module, entry_id):
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

store_mod.store = Store()
sys.modules["app.store"] = store_mod
sys.modules["app"] = types.ModuleType("app")

atp_service_mod = load("app.services.atp", "services/atp.py")
AtpService = atp_service_mod.AtpService

service = AtpService()
passed = 0


def check(name, condition, detail=""):
    global passed
    if not condition:
        raise AssertionError(f"用例失败：{name} {detail}")
    passed += 1
    print(f"  ✓ {name}")


def snapshot():
    return copy.deepcopy(store_mod.store.rows("atp"))


def restore(rows):
    table = store_mod.store.rows("atp")
    table.clear()
    table.extend(copy.deepcopy(rows))


def submit(items):
    return service.submit_inspection(copy.deepcopy(items))


def main():
    original = snapshot()
    try:
        # 场景一：同一列车一次提交多台，每台都落到自己的记录上
        rows = snapshot()
        restore(rows)
        ok, message, results = submit([
            {"id": 1, "自检状态": "正常", "制动接口": "正常", "记录数据": "排查A"},
            {"id": 2, "自检状态": "正常", "制动接口": "正常", "记录数据": "排查B"},
        ])
        check("多台提交整体成功", ok, message)
        check("生效两台", sum(1 for r in results if r["ok"] and not r["skipped"]) == 2)
        r1 = service.get_entry(1)
        r2 = service.get_entry(2)
        check("第一台写自己的记录", r1["自检状态"] == "正常" and r1["记录数据"] == "排查A")
        check("第二台写自己的记录", r2["自检状态"] == "正常" and r2["记录数据"] == "排查B")
        check("两台的制动接口都保留了提交值",
              r1["制动接口"] == "正常" and r2["制动接口"] == "正常")

        # 场景二：刷新后列表/详情不出现两套结论
        items, total = service.list_entries(page=1, size=100)
        list_map = {row["id"]: row for row in items}
        for entry_id in (1, 2):
            detail = service.get_entry(entry_id)
            check(f"id={entry_id} 列表与详情车载状态一致",
                  list_map[entry_id]["车载状态"] == detail["车载状态"] == detail["status"])
        check("复查通过后旧结论不再残留",
              list_map[2]["车载状态"] == "正常" and list_map[2]["自检状态"] == "正常")

        # 场景三：制动接口结果缺失 → 保留原值并点名是哪台缺
        restore(rows)
        ok, message, results = submit([
            {"id": 1, "自检状态": "自检异常", "记录数据": "排查C"},
        ])
        check("缺制动接口时整体仍按成功处理", ok and len(results) == 1)
        msg = results[0]["message"]
        check("提示里点名设备并说明制动接口缺失、保留原值",
              "ATP-0001" in msg and "制动接口检查结果缺失" in msg and "保留原有值" in msg, msg)
        r1 = service.get_entry(1)
        check("原制动接口值未被清空", r1["制动接口"] == "正常")
        check("提交了的自检状态正常更新", r1["自检状态"] == "自检异常")

        # 场景四：同批次重复提交同一台只生效一次
        restore(rows)
        ok, message, results = submit([
            {"id": 1, "自检状态": "正常", "制动接口": "正常"},
            {"id": 1, "自检状态": "自检异常", "制动接口": "制动异常"},
        ])
        check("重复提交不报错", ok)
        check("一台生效、一台跳过",
              sum(1 for r in results if r["ok"] and not r["skipped"]) == 1
              and sum(1 for r in results if r["skipped"]) == 1)
        check("跳过的是第二次提交", results[1]["skipped"])
        r1 = service.get_entry(1)
        check("保留第一次提交的结论",
              r1["自检状态"] == "正常" and r1["制动接口"] == "正常")

        # 场景五：一台失败不回滚已成功的其他设备
        restore(rows)
        ok, message, results = submit([
            {"id": 1, "自检状态": "正常", "制动接口": "正常", "记录数据": "已成功"},
            {"id": 2, "自检状态": "胡编的值", "制动接口": "正常"},
            {"id": 999, "自检状态": "正常", "制动接口": "正常"},
        ])
        check("存在失败设备时整体 ok=False", not ok)
        check("成功一台、失败两台",
              sum(1 for r in results if r["ok"] and not r["skipped"]) == 1
              and sum(1 for r in results if not r["ok"]) == 2)
        check("失败说明点名具体设备", "ATP-0002" in results[1]["message"])
        r1 = service.get_entry(1)
        r2 = service.get_entry(2)
        check("成功设备的结果依然在", r1["记录数据"] == "已成功" and r1["自检状态"] == "正常")
        check("失败设备原值未被改动", r2["自检状态"] == "自检异常" and r2["制动接口"] == "正常")

        # 附加：空提交与按设备编号定位
        restore(rows)
        ok, message, results = submit([])
        check("空批次被拦下", not ok)
        ok, message, results = submit([
            {"设备编号": "ATP-0002", "自检状态": "正常", "制动接口": "制动异常"},
        ])
        check("支持按设备编号定位落库", ok)
        r2 = service.get_entry(2)
        check("编号定位写入了正确记录",
              r2["自检状态"] == "正常" and r2["制动接口"] == "制动异常"
              and r2["车载状态"] == "自检异常")

        # 附加：单台动作流转后列表/详情口径一致
        restore(rows)
        entry, msg = service.run_action(2, "办理停用")
        check("停用动作生效", entry is not None and entry["车载状态"] == "已停用")
        items, _ = service.list_entries(page=1, size=100)
        check("停用后列表详情一致",
              next(i for i in items if i["id"] == 2)["车载状态"]
              == service.get_entry(2)["车载状态"] == "已停用")

        # 附加：并发两批提交不会互相覆盖（锁串行 + 各自记录）
        restore(rows)
        import threading
        errors = []

        def worker(items):
            try:
                submit(items)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=(
            [{"id": 1, "自检状态": "正常", "制动接口": "正常", "记录数据": "批次一"}],
        ))
        t2 = threading.Thread(target=worker, args=(
            [{"id": 2, "自检状态": "正常", "制动接口": "正常", "记录数据": "批次二"}],
        ))
        t1.start(); t2.start(); t1.join(); t2.join()
        check("并发提交无异常", not errors, errors)
        check("两批结果各在各的记录上",
              service.get_entry(1)["记录数据"] == "批次一"
              and service.get_entry(2)["记录数据"] == "批次二")
    finally:
        restore(original)

    print(f"\n全部 {passed} 项断言通过")


if __name__ == "__main__":
    main()
