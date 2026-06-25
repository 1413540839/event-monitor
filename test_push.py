# -*- coding: utf-8 -*-
"""Minimal push test"""
import os, requests, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

SENDKEY = os.environ.get("SENDKEY", "")
log.info("SENDKEY exists: %s", bool(SENDKEY))
log.info("SENDKEY length: %d", len(SENDKEY))

if not SENDKEY:
    log.error("NO SENDKEY!")
    exit(1)

try:
    r = requests.post(
        f"https://sctapi.ftqq.com/{SENDKEY}.send",
        data={"title": "测试推送", "desp": "如果你收到这条消息，说明管道通了！"},
        timeout=10
    )
    log.info("HTTP: %d", r.status_code)
    log.info("Response: %s", r.text[:200])
    if r.status_code == 200:
        data = r.json()
        if data.get("code") == 0:
            log.info("PUSH SUCCESS!")
        else:
            log.error("PUSH FAIL: %s", data.get("message", ""))
    else:
        log.error("HTTP FAIL")
except Exception as e:
    log.error("Exception: %s", e)
