"""Recruiter (Boss) commands: jobs, inbox, candidates, chat-history, geek-info."""

from __future__ import annotations

import csv
import io
import json
import logging

import click
from rich.panel import Panel
from rich.table import Table

from ..client import BossClient
from ..exceptions import BossApiError
from ._common import (
    console,
    handle_command,
    require_auth,
    run_client_action,
    structured_output_options,
)

logger = logging.getLogger(__name__)


# ── recruiter jobs ──────────────────────────────────────────────────

@click.command("recruiter-jobs")
@structured_output_options
def recruiter_jobs(as_json: bool, as_yaml: bool) -> None:
    """查看招聘中的职位列表"""
    cred = require_auth()

    def _render(data: list[dict]) -> None:
        if not data:
            console.print("[yellow]暂无在线职位[/yellow]")
            return

        table = Table(title=f"📋 招聘职位 ({len(data)} 个)", show_lines=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("职位", style="bold cyan", max_width=25)
        table.add_column("薪资", style="yellow", max_width=12)
        table.add_column("地区", style="blue", max_width=15)
        table.add_column("encJobId", style="dim", max_width=30)

        for i, job in enumerate(data, 1):
            table.add_row(
                str(i),
                job.get("jobName", "-"),
                job.get("salaryDesc", "-"),
                job.get("address", "-"),
                job.get("encryptJobId", "-"),
            )

        console.print(table)
        console.print("  [dim]💡 使用 boss recruiter-inbox --job <encJobId> 查看该职位的候选人[/dim]")

    handle_command(cred, action=lambda c: c.get_boss_chatted_jobs(), render=_render, as_json=as_json, as_yaml=as_yaml)


# ── recruiter inbox (candidate list) ──────────────────────────────

@click.command("recruiter-inbox")
@click.option("--job", "enc_job_id", default="", help="按职位 encryptJobId 筛选")
@click.option("--label", "label_id", default=0, type=int, help="按标签筛选 (0=全部)")
@structured_output_options
def recruiter_inbox(enc_job_id: str, label_id: int, as_json: bool, as_yaml: bool) -> None:
    """查看候选人消息列表 (招聘方沟通列表)"""
    cred = require_auth()

    def _action(c: BossClient) -> dict:
        # Step 1: get friend IDs
        friend_data = c.get_boss_friend_list(label_id=label_id, enc_job_id=enc_job_id)
        friend_list = friend_data.get("result", [])

        if not friend_list:
            return {"friendList": [], "lastMessages": []}

        friend_ids = [f["friendId"] for f in friend_list if f.get("friendId")]

        # Step 2: get friend details
        details = c.get_boss_friend_details(friend_ids)
        detail_list = details.get("friendList", [])

        # Step 3: get last messages (zpData returns list directly)
        # Only request first batch to avoid too many IDs
        batch_ids = friend_ids[:50]
        last_msgs = c.get_boss_last_messages(batch_ids)

        return {"friendList": detail_list, "lastMessages": last_msgs}

    def _render(data: dict) -> None:
        detail_list = data.get("friendList", [])
        last_msgs = data.get("lastMessages", [])

        if not detail_list:
            console.print("[yellow]暂无候选人消息[/yellow]")
            return

        # Build msg lookup
        msg_map: dict[int, dict] = {}
        if isinstance(last_msgs, list):
            for msg in last_msgs:
                uid = msg.get("uid", 0)
                if uid:
                    msg_map[uid] = msg

        table = Table(title=f"💬 候选人列表 ({len(detail_list)} 人)", show_lines=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("候选人", style="bold cyan", max_width=12)
        table.add_column("职位", style="green", max_width=20)
        table.add_column("薪资", style="yellow", max_width=10)
        table.add_column("最近消息", style="dim", max_width=30)
        table.add_column("时间", style="dim", max_width=8)

        for i, friend in enumerate(detail_list, 1):
            uid = friend.get("uid", 0)
            msg_info = msg_map.get(uid, {})
            last_text = ""
            if msg_info.get("lastMsgInfo"):
                last_text = msg_info["lastMsgInfo"].get("showText", "")[:28]

            table.add_row(
                str(i),
                friend.get("name", "-"),
                friend.get("jobName", "-"),
                friend.get("salaryDesc", friend.get("lastTime", "-")),
                last_text or "-",
                msg_info.get("lastTime", friend.get("lastTime", "-")),
            )

        console.print(table)
        console.print("  [dim]💡 使用 boss recruiter-geek <encryptGeekId> 查看候选人详情[/dim]")

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


# ── recruiter geek info ──────────────────────────────────────────

@click.command("recruiter-geek")
@click.argument("encrypt_geek_id")
@click.option("--security-id", default="", help="候选人 securityId")
@click.option("--job-id", default=0, type=int, help="关联职位 ID")
@structured_output_options
def recruiter_geek(encrypt_geek_id: str, security_id: str, job_id: int, as_json: bool, as_yaml: bool) -> None:
    """查看候选人详细信息 (需要 encryptGeekId)"""
    cred = require_auth()

    def _action(c: BossClient) -> dict:
        # If job_id not provided, try to get it from chatted jobs
        nonlocal job_id, security_id
        if not job_id:
            jobs = c.get_boss_chatted_jobs()
            if jobs:
                job_id = jobs[0].get("jobId", 0)

        if not security_id:
            # Try to find security_id from friend list
            friend_data = c.get_boss_friend_list()
            for f in friend_data.get("result", []):
                if f.get("encryptFriendId") == encrypt_geek_id:
                    friend_details = c.get_boss_friend_details([f["friendId"]])
                    for fd in friend_details.get("friendList", []):
                        security_id = fd.get("securityId", "")
                        break
                    break

        return c.get_boss_chat_geek_info(
            encrypt_geek_id=encrypt_geek_id,
            security_id=security_id,
            job_id=job_id,
        )

    def _render(data: dict) -> None:
        geek = data.get("data", data)

        name = geek.get("name", "-")
        age = geek.get("ageDesc", "-")
        gender = "男" if geek.get("gender") == 1 else "女" if geek.get("gender") == 2 else "-"
        edu = geek.get("edu", "-")
        city = geek.get("city", "-")
        salary = geek.get("salaryDesc", "-")
        expect_salary = geek.get("price", "-")
        position = geek.get("positionName", geek.get("toPosition", "-"))
        status = geek.get("positionStatus", "-")
        last_company = geek.get("lastCompany", "-")
        last_position = geek.get("lastPosition", "-")
        school = geek.get("school", "-")
        major = geek.get("major", "-")
        work_year = geek.get("year", "-")

        work_exp = geek.get("workExpList", [])
        work_lines = []
        for w in work_exp[:5]:
            work_lines.append(f"  {w.get('timeDesc', '')}  {w.get('company', '')} · {w.get('positionName', '')}")

        panel_text = (
            f"[bold cyan]{name}[/bold cyan]  {gender}  {age}\n"
            f"学历: {edu} · 工作年限: {work_year}\n"
            f"城市: {city} · 求职状态: {status}\n"
            f"\n"
            f"[bold yellow]期望薪资:[/bold yellow] {expect_salary}\n"
            f"[bold yellow]当前薪资:[/bold yellow] {salary}\n"
            f"期望职位: {position}\n"
            f"\n"
            f"[bold green]当前/最近:[/bold green] {last_company}\n"
            f"职位: {last_position}\n"
            f"学校: {school} · {major}\n"
        )

        if work_lines:
            panel_text += "\n[bold magenta]工作经历:[/bold magenta]\n" + "\n".join(work_lines)

        panel = Panel(panel_text, title="👤 候选人详情", border_style="cyan")
        console.print(panel)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


# ── recruiter chat history ──────────────────────────────────────

@click.command("recruiter-chat")
@click.argument("friend_id", type=int)
@click.option("-n", "--count", default=20, type=int, help="消息数量 (默认: 20)")
@structured_output_options
def recruiter_chat(friend_id: int, count: int, as_json: bool, as_yaml: bool) -> None:
    """查看与候选人的聊天记录 (需要 friendId)"""
    cred = require_auth()

    def _action(c: BossClient) -> dict:
        return c.get_boss_chat_history(gid=friend_id, count=count)

    def _render(data: dict) -> None:
        messages = data.get("messages", [])

        if not messages:
            console.print("[yellow]暂无聊天记录[/yellow]")
            return

        table = Table(title=f"💬 聊天记录 ({len(messages)} 条)", show_lines=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("方向", max_width=6)
        table.add_column("内容", max_width=50)
        table.add_column("类型", style="dim", max_width=6)

        for i, msg in enumerate(messages, 1):
            direction = "[cyan]←[/cyan]" if msg.get("received", True) else "[green]→[/green]"

            body = msg.get("body", {})
            if isinstance(body, str):
                text = body[:48]
            elif isinstance(body, dict):
                text = body.get("text", body.get("showText", ""))
                if not text and body.get("resume"):
                    resume = body["resume"]
                    text = f"[简历] {resume.get('user', {}).get('name', '')} {resume.get('positionCategory', '')}"
                text = text[:48] if text else "[多媒体消息]"
            else:
                text = str(body)[:48]

            msg_type = str(msg.get("type", "-"))

            table.add_row(str(i), direction, text, msg_type)

        console.print(table)

    handle_command(cred, action=_action, render=_render, as_json=as_json, as_yaml=as_yaml)


# ── recruiter labels ──────────────────────────────────────────────

@click.command("recruiter-labels")
@structured_output_options
def recruiter_labels(as_json: bool, as_yaml: bool) -> None:
    """查看候选人标签列表"""
    cred = require_auth()

    def _render(data: dict) -> None:
        labels = data.get("labels", data.get("labelList", data.get("result", [])))
        if isinstance(data, list):
            labels = data

        if not labels:
            console.print("[yellow]暂无标签[/yellow]")
            return

        table = Table(title="🏷️ 标签列表", show_lines=False)
        table.add_column("ID", style="dim", width=6)
        table.add_column("名称", style="cyan", max_width=20)

        for label in labels:
            table.add_row(
                str(label.get("labelId", label.get("id", "-"))),
                label.get("label", label.get("name", label.get("labelName", "-"))),
            )

        console.print(table)

    handle_command(cred, action=lambda c: c.get_boss_friend_labels(), render=_render, as_json=as_json, as_yaml=as_yaml)


# ── recruiter export ──────────────────────────────────────────────

@click.command("recruiter-export")
@click.option("--job", "enc_job_id", default="", help="按职位 encryptJobId 筛选")
@click.option("-o", "--output", "output_file", default=None, help="输出文件路径")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv", help="输出格式")
def recruiter_export(enc_job_id: str, output_file: str | None, fmt: str) -> None:
    """导出候选人列表为 CSV 或 JSON"""
    cred = require_auth()

    try:
        def _collect(c: BossClient) -> list[dict]:
            friend_data = c.get_boss_friend_list(enc_job_id=enc_job_id)
            friend_list = friend_data.get("result", [])

            if not friend_list:
                return []

            friend_ids = [f["friendId"] for f in friend_list if f.get("friendId")]
            details = c.get_boss_friend_details(friend_ids)
            return details.get("friendList", [])

        all_candidates = run_client_action(cred, _collect)

        if not all_candidates:
            console.print("[yellow]暂无候选人数据[/yellow]")
            return

        if fmt == "json":
            output_text = json.dumps(all_candidates, indent=2, ensure_ascii=False)
        else:
            buf = io.StringIO()
            fieldnames = ["姓名", "关联职位", "来源", "最近时间", "新牛人", "encryptUid", "securityId"]
            writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for f in all_candidates:
                source_map = {1: "搜索", 2: "推荐", 3: "打招呼", 5: "主动沟通"}
                writer.writerow({
                    "姓名": f.get("name", ""),
                    "关联职位": f.get("jobName", ""),
                    "来源": source_map.get(f.get("sourceType"), str(f.get("sourceType", ""))),
                    "最近时间": f.get("lastTime", ""),
                    "新牛人": "是" if f.get("newGeek") else "",
                    "encryptUid": f.get("encryptUid", f.get("encryptFriendId", "")),
                    "securityId": f.get("securityId", ""),
                })
            output_text = buf.getvalue()

        if output_file:
            with open(output_file, "w", encoding="utf-8-sig" if fmt == "csv" else "utf-8") as fh:
                fh.write(output_text)
            console.print(f"\n[green]✅ 已导出 {len(all_candidates)} 个候选人到 {output_file}[/green]")
        else:
            click.echo(output_text)

    except BossApiError as exc:
        console.print(f"[red]❌ 导出失败: {exc}[/red]")
        raise SystemExit(1) from None
