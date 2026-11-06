"""命令行入口 `legwork`：所有状态变更都经由这里。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as config_module
from . import daily, decisions, ego, runner, sending, sources, workflow
from .errors import HostError, PolicyError
from .simulated import SimulatedBrowser
from .store import Store, StoreError

EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_HOST = 4
STATE_FILE = "state.sqlite3"


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return _dispatch(args)
    except config_module.ConfigError as exc:
        return _error(EXIT_USAGE, exc.code, str(exc))
    except PolicyError as exc:
        return _error(EXIT_REFUSED, exc.code, str(exc))
    except (StoreError, ego.BrowserBusy) as exc:
        return _error(EXIT_REFUSED, getattr(exc, "code", "store_error"), str(exc))
    except (ego.BrowserError, HostError) as exc:
        return _error(EXIT_HOST, getattr(exc, "code", "browser_error"), str(exc))


def _dispatch(args) -> int:
    if args.command == "assets":
        return _emit(_assets())
    config_path = getattr(args, "config", None)
    config = config_module.load(config_path) if config_path else None
    if args.command == "config":
        config = _require_config(config)
        return _emit(
            {
                "account_id": config.account_id,
                "platform_user_id": config.platform_user_id,
                "city_name": config.city_name,
                "live": config.live,
                "daily_limit": config.daily_limit,
                "batch_size": config.batch_size,
                "config_digest": config.digest,
            }
        )

    data_dir = Path(args.data_dir) if args.data_dir else Path(".legwork")
    mode = "fixtures" if getattr(args, "fixtures", None) else "live"
    fixed = getattr(args, "now", None)
    if fixed is not None and mode == "live" and args.command in ("prepare", "send", "followup", "run"):
        raise PolicyError(
            "fixed_now_forbidden",
            "真实通道不接受 --now：发送日期按配置时区的实际时间判定，跨日即停止",
        )
    clock = _clock(fixed, config, mode)
    now = clock()
    with Store(data_dir / STATE_FILE) as store:
        if args.command == "prepare":
            config = _require_config(config)
            store.require_mode(mode)
            return _emit(workflow.prepare(store, config, _source(args, config), now))
        if args.command == "packet":
            config = _require_config(config)
            run = _require_run(store, args.run_id)
            return _emit(workflow.build_packet(store, config, run, data_dir, now))
        if args.command == "decide":
            config = _require_config(config)
            run = _require_run(store, args.run_id)
            return _emit(
                decisions.accept_decisions(store, config, run, args.packet, args.results, now)
            )
        if args.command == "finalize":
            config = _require_config(config)
            run = _require_run(store, args.run_id)
            return _emit(workflow.finish_prepare(store, config, run, now))
        if args.command == "send":
            config = _require_config(config)
            run = _require_source(store, args.run_id, mode)
            store.require_mode(mode)
            payload, code = sending.send_ready(
                store,
                config,
                run,
                _browser(args, config),
                clock=clock,
                data_dir=data_dir,
                job_ids=args.job or None,
            )
            _print(payload)
            return code
        if args.command == "followup":
            config = _require_config(config)
            run = _require_source(store, args.run_id, mode)
            store.require_mode(mode)
            payload, code = sending.follow_up(
                store,
                config,
                run,
                _browser(args, config),
                clock=clock,
                data_dir=data_dir,
                job_ids=args.job or None,
            )
            _print(payload)
            return code
        if args.command == "today":
            day = args.day or now.date().isoformat()
            payload = daily.build(store, config, day)
            sys.stdout.write(daily.render(payload, args.job) + "\n")
            return 0
        if args.command == "status":
            return _emit(workflow.status(store, config, now))
        if args.command == "run":
            config = _require_config(config)
            store.require_mode(mode)
            payload, code = runner.run_once(
                store,
                config,
                _source(args, config),
                data_dir=data_dir,
                clock=clock,
                browser=_browser(args, config) if config.live else None,
                resume_run=args.resume_run,
                host_command=args.host_command,
                workdir=args.workdir,
                prepare_only=args.prepare_only,
            )
            _print(payload)
            return code
    raise PolicyError("unknown_command", f"未知命令：{args.command}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legwork",
        description="Legwork：本机运行的求职执行工具。程序执行固定动作，语义判断交给宿主 agent。",
    )
    parser.add_argument("--data-dir", help="状态目录，默认 ./.legwork")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("config", help="配置相关命令")
    check_sub = check.add_subparsers(dest="config_command", required=True)
    check_run = check_sub.add_parser("check", help="校验配置并输出绑定摘要")
    check_run.add_argument("--config", required=True)

    prepare = sub.add_parser("prepare", help="采集候选、去重并建立运行")
    prepare.add_argument("--config", required=True)
    prepare.add_argument("--fixtures", help="离线 fixture 目录；与真实平台隔离")
    prepare.add_argument("--now", help="固定当前时间，仅用于测试")

    packet = sub.add_parser("packet", help="导出一批岗位与简历事实")
    packet.add_argument("--config", required=True)
    packet.add_argument("--run-id", required=True)

    decide = sub.add_parser("decide", help="校验并接收模型决策")
    decide.add_argument("--config", required=True)
    decide.add_argument("--run-id", required=True)
    decide.add_argument("--packet", required=True)
    decide.add_argument("--results", required=True)
    decide.add_argument("--now", help="固定当前时间，仅用于测试")

    finalize = sub.add_parser(
        "finalize", help="核对准备完成：候选已处理完或预算耗尽才允许发送"
    )
    finalize.add_argument("--config", required=True)
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--now", help="固定当前时间，仅用于测试")

    send = sub.add_parser("send", help="按已接收决策执行首次沟通")
    send.add_argument("--config", required=True)
    send.add_argument("--run-id", required=True)
    send.add_argument("--fixtures", help="离线 fixture 目录；模拟发送，不操作真实浏览器")
    send.add_argument("--job", action="append", help="只发送指定岗位（可重复）")
    send.add_argument("--now", help="固定当前时间，仅用于测试")

    followup = sub.add_parser(
        "followup", help="对结果不明的岗位向已存在会话补发定制正文（不发新联系人）"
    )
    followup.add_argument("--config", required=True)
    followup.add_argument("--run-id", required=True)
    followup.add_argument(
        "--job", action="append", required=True, help="要补发的岗位 ID（可重复）"
    )
    followup.add_argument("--fixtures", help="离线 fixture 目录；模拟补发，不操作真实浏览器")
    followup.add_argument("--now", help="固定当前时间，仅用于测试")

    today = sub.add_parser(
        "today", help="查看今日投递清单：一岗一行的本地汇总，不调用模型或浏览器"
    )
    today.add_argument("--config")
    today.add_argument("--day", help="查询日期 YYYY-MM-DD，默认配置时区的今天")
    today.add_argument("--job", help="查看单个岗位的完整 JD、话术与执行结果")
    today.add_argument("--now", help="固定当前时间，仅用于测试")

    status = sub.add_parser("status", help="输出简短统计")
    status.add_argument("--config")
    status.add_argument("--now", help="固定当前时间，仅用于测试")

    run = sub.add_parser("run", help="单次定时入口：准备、调用宿主、校验、发送")
    run.add_argument("--config", required=True)
    run.add_argument("--fixtures", help="离线 fixture 目录；模拟整条链路")
    run.add_argument("--resume-run", help="继续既有未完成运行")
    run.add_argument(
        "--host-command",
        default="codex",
        help="宿主命令，需兼容 codex exec 接口；默认 codex，测试时替换为替身",
    )
    run.add_argument("--workdir", help="宿主工作目录，默认当前目录")
    run.add_argument(
        "--prepare-only", action="store_true", help="只完成含模型的准备，不进入发送阶段"
    )
    run.add_argument("--now", help="固定当前时间，仅用于测试")

    sub.add_parser("assets", help="打印随包资源路径（schema、JS、Skills）")
    return parser


def _source(args, config):
    fixtures = getattr(args, "fixtures", None)
    if fixtures:
        return sources.FixtureSource(fixtures)
    if not config.live:
        raise PolicyError(
            "live_disabled",
            "读取真实平台需要配置 live=true；离线验证请用 --fixtures，两者不混用",
        )
    return sources.EgoSource(ego.EgoBrowser(config))


def _browser(args, config):
    fixtures = getattr(args, "fixtures", None)
    if fixtures:
        return SimulatedBrowser(fixtures)
    if not config.live:
        raise PolicyError("live_disabled", "配置 live=false：不会操作真实浏览器")
    return ego.EgoBrowser(config)


def _require_config(config) -> config_module.Config:
    if config is None:
        raise PolicyError("missing_config", "该命令需要 --config")
    return config


def _require_run(store, run_id: str) -> dict:
    run = store.run(run_id)
    if run is None:
        raise PolicyError("unknown_run", f"没有这个运行：{run_id}")
    return run


def _require_source(store, run_id: str, mode: str) -> dict:
    """运行与当前通道必须一致：模拟任务不会被真实通道接手，反之亦然。"""
    run = _require_run(store, run_id)
    if run["source"] != mode:
        raise PolicyError(
            "source_mismatch",
            f"该运行建立于 {run['source']} 通道，不能由 {mode} 通道继续",
        )
    return run


def _clock(value, config, mode):
    """真实通道每次取实际时间；离线通道允许 --now 固定时间用于测试。"""
    zone = config.zone() if config is not None else None
    if value is None:
        return lambda: datetime.now(zone) if zone else datetime.now().astimezone()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone or ZoneInfo("UTC"))
    fixed = parsed.astimezone(zone) if zone else parsed
    return lambda: fixed


def _assets() -> dict:
    js_files = ["js/ego_page_state.js", "js/ego_click_guard.js", "js/ego_collect.js"]
    skills = {
        "configure": "skills/legwork-configure/SKILL.md",
        "run": "skills/legwork-run/SKILL.md",
    }
    for relative in js_files:
        ego.resource_text(relative)
    for relative in skills.values():
        ego.resource_text(relative)
    return {
        "schema": str(ego.resource_path("schema/model_result.schema.json")),
        "js": [str(ego.resource_path(relative)) for relative in js_files],
        "skills": {name: str(ego.resource_path(relative)) for name, relative in skills.items()},
    }


def _emit(payload) -> int:
    _print(payload)
    return 0


def _print(payload) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _error(code: int, error_code: str, message: str) -> int:
    sys.stderr.write(
        json.dumps(
            {"error": {"code": error_code, "message": message}}, ensure_ascii=False
        )
        + "\n"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
