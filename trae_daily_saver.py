#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Trae CN 每日对话自动保存 + AI 日报生成工具
基于 trae-db-decrypt 项目的数据库解密方案，自动导出当日对话记录为 Markdown 文件
并通过 AI 模型生成每日工作报告

即插即用：所有路径均相对于脚本所在目录，无需手动配置

依赖:  pip install pycryptodome requests

快速开始:
  1. 复制 .env.example 为 .env，填入 API Key
  2. 首次使用扫描密钥: python trae_daily_saver.py --scan-key
  3. 生成今日日报:     python trae_daily_saver.py

@author Zhenyu Cai
@created 2026-05-20
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import hmac as hmac_mod
import json
import os
import re
import sqlite3
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from Crypto.Cipher import AES

# ==================== 脚本根目录 ====================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file():
    """
    加载 .env 配置文件到环境变量（零依赖，纯 Python 实现）

    查找顺序:
    1. 脚本同目录下的 .env
    2. 当前工作目录下的 .env

    @author Zhenyu Cai
    @created 2026-05-26
    """
    env_paths = [
        os.path.join(SCRIPT_DIR, '.env'),
        os.path.join(os.getcwd(), '.env'),
    ]
    for env_path in env_paths:
        if not os.path.isfile(env_path):
            continue
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and value and key not in os.environ:
                    os.environ[key] = value
        break


load_env_file()

# ==================== 常量配置 ====================

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80
SQLITE_HDR = b'SQLite format 3\x00'

MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}

DEFAULT_KEY_FILE = os.path.join(SCRIPT_DIR, "decrypted_key.json")
DEFAULT_DECRYPTED_DB = os.path.join(SCRIPT_DIR, "database_decrypted.db")
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "trae_dialogues")
DEFAULT_DAILY_REPORT_FILE = "daily_report.md"

# AI 模型配置（优先从 .env 读取，其次从环境变量，最后使用默认值）
DEFAULT_API_BASE = os.environ.get(
    "DEEPSEEK_API_BASE",
    os.environ.get("OPENAI_API_BASE", "https://api.deepseek.com/v1")
)
DEFAULT_API_KEY = os.environ.get("DEEPSEEK_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
DEFAULT_MODEL = os.environ.get(
    "DEEPSEEK_MODEL",
    os.environ.get("REPORT_MODEL", "deepseek-chat")
)
DEFAULT_SYSTEM_PROMPT = os.environ.get("REPORT_PROMPT",
    "依据对话内容总结今日工作日报，字数保持在 200 字上下。"
    "重点提炼今日技术优化、功能调试、方案研讨等相关工作内容，"
    "清晰说明各项任务的推进状态与工作成果。"
    "行文采用职场正式口吻，全程规避英文标识、代码、函数名称等技术细节，"
    "只描述实际工作行为与业务进展，结构完整、表述简洁。"
)


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64),
        ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD),
        ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("_pad2", wt.DWORD),
    ]


def find_trae_database():
    """自动查找 Trae CN 数据库路径"""
    appdata = os.environ.get("APPDATA", "")
    default_path = Path(appdata) / "Trae CN" / "ModularData" / "ai-agent" / "database.db"
    if default_path.exists():
        return str(default_path)

    # 尝试搜索更多路径
    candidates = [
        Path(appdata) / "Trae CN" / "ModularData" / "ai-agent" / "database.db",
        Path.home() / "AppData" / "Roaming" / "Trae CN" / "ModularData" / "ai-agent" / "database.db",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def get_ai_agent_pid():
    """查找加载了 ai_agent.dll 的 Trae CN 进程 PID"""
    try:
        out = subprocess.check_output(
            'tasklist /FI "IMAGENAME eq Trae CN.exe" /FO CSV /NH',
            shell=True, text=True, errors='replace', timeout=5
        )
    except Exception:
        return None

    pids = []
    for line in out.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.strip('"').split('","')
        if len(parts) >= 5:
            try:
                pid = int(parts[1])
                mem = int(parts[4].replace(',', '').replace(' K', '').strip() or '0')
                pids.append((pid, mem))
            except Exception:
                pass

    if not pids:
        print("[!] 未找到正在运行的 Trae CN 进程")
        return None

    pids.sort(key=lambda x: x[1], reverse=True)
    for pid, mem in pids:
        try:
            out = subprocess.check_output(
                f'tasklist /FI "PID eq {pid}" /M /FO CSV /NH',
                shell=True, text=True, errors='replace', timeout=5
            )
            if 'ai_agent' in out.lower():
                print(f"[+] 找到 ai_agent.dll 在 PID {pid} 进程中 ({mem // 1024}MB)")
                return pid
        except Exception:
            pass
    return None


def scan_memory_for_key(db_path):
    """从 Trae CN 进程内存中扫描提取 SQLCipher 密钥"""
    kernel32 = ctypes.windll.kernel32

    db_info = load_database_info(db_path)
    if not db_info:
        print("[!] 无法读取数据库文件")
        return None

    salt_hex = db_info["salt"]
    page1 = db_info["page1"]
    print(f"[+] 数据库: {db_path}")
    print(f"[+] Salt: {salt_hex}")

    pid = get_ai_agent_pid()
    if not pid:
        return None

    h = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
    if not h:
        print(f"[!] 无法打开进程 PID={pid}")
        return None

    try:
        regions = enum_memory_regions(h)
        total_mb = sum(s for _, s in regions) / 1024 / 1024
        print(f"[+] 进程 PID={pid}: {total_mb:.0f}MB, {len(regions)} 个内存区域")

        patterns = [
            re.compile(rb"x'([0-9a-fA-F]{64,192})'"),
            re.compile(rb"'([0-9a-fA-F]{64})'"),
            re.compile(rb"([0-9a-fA-F]{64})"),
        ]

        total_bytes = sum(s for _, s in regions)
        scanned = 0
        candidates = []

        for reg_idx, (base, size) in enumerate(regions):
            data = read_memory(h, base, size)
            scanned += size
            if not data:
                continue

            for pat in patterns:
                for m in pat.finditer(data):
                    hex_str = m.group(1).decode() if m.lastindex else m.group(0).decode()
                    hex_len = len(hex_str)
                    enc_key_hex = None
                    matched_salt = None

                    if hex_len == 96:
                        enc_key_hex = hex_str[:64]
                        matched_salt = hex_str[64:]
                    elif hex_len == 64:
                        enc_key_hex = hex_str
                        matched_salt = salt_hex
                    elif hex_len > 96 and hex_len % 2 == 0:
                        enc_key_hex = hex_str[:64]
                        matched_salt = hex_str[-32:]
                    else:
                        continue

                    if matched_salt == salt_hex:
                        enc_key = bytes.fromhex(enc_key_hex)
                        if verify_enc_key(enc_key, page1):
                            print(f"\n  [FOUND] 密钥验证通过!")
                            print(f"    enc_key={enc_key_hex}")
                            result = {
                                "db_path": db_path,
                                "salt": salt_hex,
                                "enc_key": enc_key_hex,
                            }
                            with open(DEFAULT_KEY_FILE, "w") as f:
                                json.dump(result, f, indent=2)
                            print(f"[+] 密钥已保存到 {DEFAULT_KEY_FILE}")
                            return result
                        else:
                            candidates.append(enc_key_hex)

            if (reg_idx + 1) % 100 == 0:
                progress = scanned / total_bytes * 100 if total_bytes else 100
                print(f"  [{progress:.1f}%] 已扫描 {scanned // 1024 // 1024}MB, {len(candidates)} 个候选")

        print(f"\n[!] 未找到有效密钥，{len(candidates)} 个候选均未通过 HMAC 验证")
        return None
    finally:
        kernel32.CloseHandle(h)


def enum_memory_regions(h):
    """枚举进程的可读已提交内存区域"""
    kernel32 = ctypes.windll.kernel32
    regs = []
    addr = 0
    mbi = MBI()
    while addr < 0x7FFFFFFFFFFF:
        if kernel32.VirtualQueryEx(h, ctypes.c_uint64(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        if mbi.State == MEM_COMMIT and mbi.Protect in READABLE and 0 < mbi.RegionSize < 500 * 1024 * 1024:
            regs.append((mbi.BaseAddress, mbi.RegionSize))
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= addr:
            break
        addr = nxt
    return regs


def read_memory(h, addr, sz):
    """从进程内存中读取数据"""
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(sz)
    n = ctypes.c_size_t(0)
    if kernel32.ReadProcessMemory(h, ctypes.c_uint64(addr), buf, sz, ctypes.byref(n)):
        return buf.raw[:n.value]
    return None


def load_database_info(db_path):
    """读取数据库第一页（含 Salt）"""
    if not os.path.exists(db_path):
        return None
    with open(db_path, "rb") as f:
        page1 = f.read(PAGE_SZ)
    salt = page1[:SALT_SZ]
    return {"path": db_path, "page1": page1, "salt": salt.hex()}


def verify_enc_key(enc_key, db_page1):
    """用 HMAC-SHA512 验证密钥是否正确"""
    try:
        salt = db_page1[:SALT_SZ]
        mac_salt = bytes(b ^ 0x3A for b in salt)
        mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
        hmac_data = db_page1[SALT_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
        stored_hmac = db_page1[PAGE_SZ - HMAC_SZ: PAGE_SZ]
        hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
        hm.update(struct.pack("<I", 1))
        return hm.digest() == stored_hmac
    except Exception:
        return False


def derive_mac_key(enc_key, salt):
    """派生 MAC 密钥"""
    mac_salt = bytes(b ^ 0x3a for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def decrypt_page(enc_key, page_data, pgno):
    """解密单个数据库页面"""
    iv = page_data[PAGE_SZ - RESERVE_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
    if pgno == 1:
        encrypted = page_data[SALT_SZ: PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return bytes(bytearray(SQLITE_HDR + decrypted + b'\x00' * RESERVE_SZ))
    else:
        encrypted = page_data[:PAGE_SZ - RESERVE_SZ]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)
        return decrypted + b'\x00' * RESERVE_SZ


def decrypt_database(db_path, key_data, output_path=DEFAULT_DECRYPTED_DB):
    """解密整个 SQLCipher 4 数据库"""
    enc_key = bytes.fromhex(key_data["enc_key"])
    print(f"[+] 解密密钥: {key_data['enc_key'][:16]}...")
    print(f"[+] 源数据库: {db_path} ({os.path.getsize(db_path)/1024/1024:.1f}MB)")

    with open(db_path, 'rb') as f:
        page1 = f.read(PAGE_SZ)

    salt = page1[:SALT_SZ]
    mac_key = derive_mac_key(enc_key, salt)
    p1_hmac_data = page1[SALT_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
    p1_stored_hmac = page1[PAGE_SZ - HMAC_SZ: PAGE_SZ]
    hm = hmac_mod.new(mac_key, p1_hmac_data, hashlib.sha512)
    hm.update(struct.pack('<I', 1))

    if hm.digest() != p1_stored_hmac:
        print("[!] HMAC 验证失败! 密钥无效")
        return None

    print("[+] HMAC 验证通过!")

    file_size = os.path.getsize(db_path)
    total_pages = file_size // PAGE_SZ
    print(f"[+] 共 {total_pages} 页，开始解密...")

    with open(db_path, 'rb') as fin, open(output_path, 'wb') as fout:
        for pgno in range(1, total_pages + 1):
            page = fin.read(PAGE_SZ)
            if len(page) < PAGE_SZ:
                page = page + b'\x00' * (PAGE_SZ - len(page))
            fout.write(decrypt_page(enc_key, page, pgno))

    print(f"[+] 解密完成: {output_path}")
    return output_path


def get_or_decrypt_db(key_file=DEFAULT_KEY_FILE, decrypted_db=DEFAULT_DECRYPTED_DB):
    """获取解密后的数据库路径，必要时执行解密"""
    # 如果已有解密数据库，直接返回
    if os.path.exists(decrypted_db):
        print(f"[+] 使用已有解密数据库: {decrypted_db}")
        return decrypted_db

    # 查找原始数据库
    db_path = find_trae_database()
    if not db_path:
        print("[!] 未找到 Trae CN 数据库")
        print("    预期路径: %APPDATA%/Trae CN/ModularData/ai-agent/database.db")
        return None

    # 加载密钥
    if not os.path.exists(key_file):
        print(f"[!] 密钥文件不存在: {key_file}")
        print("    请先运行: python trae_daily_saver.py --scan-key")
        return None

    with open(key_file) as f:
        key_data = json.load(f)

    # 解密
    return decrypt_database(db_path, key_data, decrypted_db)


def extract_daily_conversations(decrypted_db, target_date):
    """从解密后的数据库中提取指定日期的对话记录"""
    conn = sqlite3.connect(decrypted_db)
    tz_cn = timezone(timedelta(hours=8))

    day_start = int(datetime(target_date.year, target_date.month, target_date.day,
                             tzinfo=tz_cn).timestamp())
    day_end = int(datetime(target_date.year, target_date.month, target_date.day,
                           23, 59, 59, tzinfo=tz_cn).timestamp())

    # 查询当天的所有 history_v2 记录，关联 session 和 project 信息
    query = """
        SELECT
            h.history_v2_id,
            h.session_id,
            h.messages,
            h.agent_type,
            h.token_usage,
            h.created_at,
            s.session_title,
            s.session_type,
            p.absolute_path as project_path
        FROM history_v2 h
        LEFT JOIN chat_session s ON h.session_id = s.session_id
        LEFT JOIN session_project sp ON h.session_id = sp.session_id
        LEFT JOIN project p ON sp.project_id = p.project_id
        WHERE h.created_at >= ? AND h.created_at < ? AND h.messages IS NOT NULL
        ORDER BY h.created_at ASC
    """

    rows = conn.execute(query, (day_start, day_end)).fetchall()
    conn.close()

    print(f"[+] 找到 {len(rows)} 条对话记录 ({target_date.strftime('%Y-%m-%d')})")

    conversations = []
    for row in rows:
        history_v2_id, session_id, messages_raw, agent_type, token_usage, created_at, \
            session_title, session_type, project_path = row

        try:
            messages_data = json.loads(messages_raw)
        except (json.JSONDecodeError, TypeError):
            continue

        conv_time = datetime.fromtimestamp(created_at, tz=tz_cn).strftime('%H:%M:%S')

        conversations.append({
            'time': conv_time,
            'session_id': session_id,
            'session_title': session_title or '无标题',
            'session_type': session_type or 'unknown',
            'agent_type': agent_type or 'unknown',
            'token_usage': token_usage or 0,
            'project_path': project_path or '未知项目',
            'messages': messages_data
        })

    # 按 session_id 分组
    sessions = {}
    for conv in conversations:
        sid = conv['session_id']
        if sid not in sessions:
            sessions[sid] = {
                'session_title': conv['session_title'],
                'session_type': conv['session_type'],
                'project_path': conv['project_path'],
                'conversations': []
            }
        sessions[sid]['conversations'].append(conv)

    return sessions


def parse_messages(messages_data):
    """解析 messages JSON 为可读的对话文本"""
    lines = []

    if isinstance(messages_data, dict):
        raw_messages = messages_data.get('raw_messages', messages_data.get('messages', []))
        if not raw_messages and 'content' in messages_data:
            raw_messages = [messages_data]
    elif isinstance(messages_data, list):
        raw_messages = messages_data
    else:
        return ["[无法解析的消息格式]"]

    for msg in raw_messages:
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')

        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get('type') == 'text':
                        text_parts.append(part.get('text', ''))
                    elif part.get('type') == 'tool_use':
                        tool_name = part.get('name', 'unknown_tool')
                        tool_input = part.get('input', {})
                        text_parts.append(f"\n**[工具调用: {tool_name}]**\n```json\n{json.dumps(tool_input, ensure_ascii=False, indent=2)}\n```\n")
                    elif part.get('type') == 'tool_result':
                        text_parts.append(f"\n**[工具结果]**\n{str(part.get('content', ''))[:500]}...\n")
                    else:
                        text_parts.append(str(part))
                else:
                    text_parts.append(str(part))
            content = '\n'.join(text_parts)

        if role == 'user':
            lines.append(f"### 👤 用户\n\n{content}\n")
        elif role == 'assistant':
            lines.append(f"### 🤖 AI 助手\n\n{content}\n")
        elif role == 'system':
            lines.append(f"### ⚙️ 系统\n\n```\n{content[:300]}\n```\n")
        elif role == 'tool':
            lines.append(f"### 🔧 工具\n\n```\n{str(content)[:300]}\n```\n")

    return lines


def format_as_markdown(sessions, target_date):
    """将会话格式化为 Markdown 文档"""
    date_str = target_date.strftime('%Y年%m月%d日')
    lines = [
        f"# Trae CN 对话记录 - {date_str}",
        "",
        f"> 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 共 {len(sessions)} 个会话",
        "",
        "---",
        ""
    ]

    total_tokens = 0

    for idx, (sid, session_data) in enumerate(sessions.items(), 1):
        title = session_data['session_title']
        stype = session_data['session_type']
        project = session_data['project_path']
        project_name = Path(project).name if project and project != '未知项目' else '未知项目'

        session_tokens = sum(c['token_usage'] for c in session_data['conversations'])
        total_tokens += session_tokens

        lines.extend([
            f"## 会话 {idx}: {title}",
            "",
            f"| 属性 | 值 |",
            f"|------|-----|",
            f"| 会话ID | `{sid}` |",
            f"| 类型 | {stype} |",
            f"| 项目 | {project_name} |",
            f"| Token 用量 | {session_tokens:,} |",
            "",
            "---",
            ""
        ])

        for conv in session_data['conversations']:
            lines.append(f"### ⏰ {conv['time']}")
            lines.append("")
            msg_lines = parse_messages(conv['messages'])
            lines.extend(msg_lines)
            lines.append("---\n")

    lines.insert(0, f"> Token 总用量: {total_tokens:,}")

    return '\n'.join(lines)


def extract_conversation_summary(sessions):
    """
    从会话数据中提取精简摘要文本，用于 AI 模型生成日报

    提取策略：每轮对话只取用户请求和前200字AI回复，控制总长度在模型上下文窗口内

    @author Zhenyu Cai
    @created 2026-05-20
    """
    lines = []
    total_sessions = len(sessions)
    total_tokens = 0

    for idx, (sid, session_data) in enumerate(sessions.items(), 1):
        title = session_data['session_title']
        project = session_data['project_path']
        project_name = Path(project).name if project and project != '未知项目' else '未知项目'

        lines.append(f"## 会话{idx}: {title} (项目: {project_name})")

        for conv in session_data['conversations']:
            time_str = conv['time']
            token_usage = conv.get('token_usage', 0)
            total_tokens += token_usage

            messages_data = conv['messages']
            if isinstance(messages_data, dict):
                raw_messages = messages_data.get('raw_messages', messages_data.get('messages', []))
                if not raw_messages and 'content' in messages_data:
                    raw_messages = [messages_data]
            elif isinstance(messages_data, list):
                raw_messages = messages_data
            else:
                continue

            for msg in raw_messages:
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')

                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get('type') == 'text':
                            text_parts.append(part.get('text', ''))
                        elif isinstance(part, dict) and part.get('type') == 'tool_use':
                            text_parts.append(f'[工具调用:{part.get("name","")}]')
                        elif isinstance(part, dict) and part.get('type') == 'tool_result':
                            text_parts.append('[工具结果]')
                    content = ' '.join(text_parts)

                if role == 'user':
                    # 用户消息：截取前300字
                    text = str(content).replace('\n', ' ')[:300]
                    lines.append(f">>> 用户 ({time_str}): {text}")
                elif role == 'assistant':
                    # AI 回复：只取前200字摘要
                    text = str(content).replace('\n', ' ')[:200]
                    lines.append(f"<<< AI ({time_str}): {text}")

        lines.append("")

    lines.insert(0, f"今日共 {total_sessions} 个会话，Token总用量约 {total_tokens:,}")
    lines.insert(0, "")
    return '\n'.join(lines)


def generate_daily_report(sessions, target_date, api_base, api_key, model,
                          system_prompt=DEFAULT_SYSTEM_PROMPT):
    """
    调用 AI 模型生成每日工作日报

    @author Zhenyu Cai
    @created 2026-05-20

    Args:
        sessions: 当日所有会话数据
        target_date: 目标日期
        api_base: API 地址
        api_key: API 密钥
        model: 模型名称
        system_prompt: 自定义系统提示词

    Returns:
        str: 生成的日报 Markdown 内容，失败返回 None
    """
    if not api_key:
        print("[!] 未设置 API Key，跳过日报生成")
        print("    请设置环境变量 OPENAI_API_KEY 或使用 --api-key 参数")
        return None

    summary_text = extract_conversation_summary(sessions)

    user_prompt = (
        f"以下是 {target_date.strftime('%Y年%m月%d日')} 的AI编程助手对话记录摘要：\n\n{summary_text}"
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 2000
    }

    print(f"\n[+] 正在调用 {model} 模型生成日报...")
    print(f"    摘要长度: {len(summary_text):,} 字符")

    try:
        response = requests.post(
            f"{api_base}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )
        response.raise_for_status()
        data = response.json()

        report = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        print(f"[+] 日报生成完成 "
              f"(Prompt: {usage.get('prompt_tokens', '?')}, "
              f"Completion: {usage.get('completion_tokens', '?')})")

        return report

    except requests.exceptions.Timeout:
        print(f"[!] API 请求超时（120秒）")
        return None
    except requests.exceptions.RequestException as e:
        print(f"[!] API 请求失败: {str(e)}")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        print(f"[!] API 响应解析失败: {str(e)}")
        return None


def save_daily_dialogues(output_dir, target_date=None, key_file=DEFAULT_KEY_FILE,
                          decrypted_db=DEFAULT_DECRYPTED_DB, generate_report=True,
                          api_base=DEFAULT_API_BASE, api_key=DEFAULT_API_KEY,
                          model=DEFAULT_MODEL, system_prompt=DEFAULT_SYSTEM_PROMPT):
    """
    主函数：保存当天的对话记录到本地文件，并可选择调用 AI 生成日报总结

    @author Zhenyu Cai
    @created 2026-05-20
    @updated 2026-05-20: 添加 AI 日报生成功能
    """
    if target_date is None:
        target_date = datetime.now(timezone(timedelta(hours=8))).date()

    print("=" * 60)
    print("  Trae CN 每日对话自动保存工具")
    print(f"  目标日期: {target_date.strftime('%Y-%m-%d')}")
    print("=" * 60)

    # 获取解密数据库
    db = get_or_decrypt_db(key_file, decrypted_db)
    if not db:
        print("[!] 无法获取解密数据库，请先运行 --scan-key 提取密钥")
        return False

    # 提取对话
    sessions = extract_daily_conversations(db, target_date)

    if not sessions:
        print(f"[!] {target_date.strftime('%Y-%m-%d')} 没有对话记录")
        return False

    # 格式化并保存对话记录
    markdown_content = format_as_markdown(sessions, target_date)

    os.makedirs(output_dir, exist_ok=True)
    filename = f"trae_dialogues_{target_date.strftime('%Y%m%d')}.md"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(markdown_content)

    print(f"\n[+] 对话记录已保存到: {filepath}")
    print(f"[+] 共 {len(sessions)} 个会话, 文件大小: {len(markdown_content):,} 字符")

    # 生成 AI 日报总结
    if generate_report:
        print("\n" + "-" * 40)
        print("  开始生成AI日报总结...")
        print("-" * 40)

        report = generate_daily_report(sessions, target_date, api_base, api_key, model, system_prompt)
        if report:
            # 组装完整日报文件
            date_str = target_date.strftime('%Y年%m月%d日')
            full_report = (
                f"# 每日工作报告 - {date_str}\n\n"
                f"> 基于 Trae CN 当日 {len(sessions)} 个会话自动生成\n"
                f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"---\n\n"
                f"{report}\n"
            )

            report_path = os.path.join(output_dir, DEFAULT_DAILY_REPORT_FILE)
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(full_report)

            print(f"\n[+] 日报已保存到: {report_path}")
            print(f"[+] 文件大小: {len(full_report):,} 字符")

    return True


def main():
    parser = argparse.ArgumentParser(description="Trae CN 每日对话自动保存工具")
    parser.add_argument("--scan-key", action="store_true",
                        help="扫描 Trae CN 进程内存提取加密密钥")
    parser.add_argument("--key-file", default=DEFAULT_KEY_FILE,
                        help=f"密钥文件路径（默认: {DEFAULT_KEY_FILE}）")
    parser.add_argument("--db-path",
                        help="加密数据库路径（默认自动检测）")
    parser.add_argument("--decrypted-db", default=DEFAULT_DECRYPTED_DB,
                        help=f"解密后数据库路径（默认: {DEFAULT_DECRYPTED_DB}）")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help=f"对话记录输出目录（默认: {DEFAULT_OUTPUT_DIR}）")
    parser.add_argument("--date",
                        help="导出指定日期的对话 (格式: YYYY-MM-DD，默认今天)")
    parser.add_argument("--no-summary", action="store_true",
                        help="跳过 AI 日报生成，仅导出原始对话")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE,
                        help=f"OpenAI 兼容 API 地址（默认: {DEFAULT_API_BASE}）")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY,
                        help="OpenAI 兼容 API Key（默认: 环境变量 OPENAI_API_KEY）")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"生成日报使用的模型（默认: {DEFAULT_MODEL}）")
    parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT,
                        help="自定义 AI 日报生成提示词")

    args = parser.parse_args()

    if args.scan_key:
        db_path = args.db_path or find_trae_database()
        if not db_path:
            print("[!] 未找到 Trae CN 数据库，请手动指定 --db-path")
            sys.exit(1)
        result = scan_memory_for_key(db_path)
        if result:
            print(f"\n[+] 密钥已保存在 {DEFAULT_KEY_FILE}，现在可以运行导出命令")
        sys.exit(0 if result else 1)

    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, '%Y-%m-%d').date()
        except ValueError:
            print(f"[!] 日期格式错误: {args.date}，请使用 YYYY-MM-DD 格式")
            sys.exit(1)

    success = save_daily_dialogues(
        output_dir=args.output_dir,
        target_date=target_date,
        key_file=args.key_file,
        decrypted_db=args.decrypted_db,
        generate_report=not args.no_summary,
        api_base=args.api_base,
        api_key=args.api_key,
        model=args.model,
        system_prompt=args.system_prompt
    )

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
