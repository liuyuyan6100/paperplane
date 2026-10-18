"""Retain late delivery evidence without committing an obsolete execution."""

import json

from studio.db import dumps, event, now
from studio.domain.tasks import ExecutionLease


def retain_late_result(db, job_id: str, lease: ExecutionLease, result: dict):
    current = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not current:
        return
    receipt_id = str(result.get("external_id") or "")[:4000]
    message = f"第 {lease.attempt} 次执行晚到；保留回执线索，未覆盖当前执行结果"
    if receipt_id:
        message += "；平台编号：" + receipt_id
    # A queued retry of the SAME attempt must be held when its old request
    # subsequently reports delivery. Otherwise eventual consistency can resend.
    if (
        lease.same_attempt(current)
        and current["status"] in ("uncertain", "queued", "failed")
        and (receipt_id or result.get("status") in ("draft", "published"))
    ):
        snapshot = json.loads(current["snapshot"])
        for source, target in (
            ("verification", "wechat_receipt"),
            ("desktop_receipt", "desktop_receipt"),
            ("xhs_receipt", "xhs_receipt"),
        ):
            if isinstance(result.get(source), dict) and not snapshot.get(target):
                snapshot[target] = result[source]
        note = "旧请求在中断后返回回执，请先核对原平台结果；未自动重新发送"
        db.execute(
            "UPDATE jobs SET status='uncertain',run_token='',external_id=?,snapshot=?,"
            "error=?,updated_at=? WHERE id=?",
            (current["external_id"] or receipt_id, dumps(snapshot), note, now(), job_id),
        )
        message = note + ("；平台编号：" + receipt_id if receipt_id else "")
    event(db, job_id, "uncertain", message)
