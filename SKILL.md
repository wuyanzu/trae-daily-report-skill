---
name: "daily-report"
description: "Generates daily work report from Trae CN chat history. Invoke when user types /日报 or asks for daily report/工作日报."
---

# Daily Report Generator

This skill automates daily work report generation by extracting Trae CN conversation history and summarizing it via AI.

## Trigger

Invoke immediately when user types `/日报`, asks for "日报", "生成日报", "今日工作报告", or "daily report".

## Execution Steps

### Step 1: Run the daily report script

```bash
cd d:\下载\python_scripts && python trae_daily_saver.py --no-summary
```

Wait for the script to complete. It will:
- Decrypt the Trae CN database
- Extract today's conversations
- Save raw dialogue markdown to `d:\下载\python_scripts\trae_dialogues\trae_dialogues_YYYYMMDD.md`

If the script fails because no key file exists, ask the user to first run:
```bash
cd d:\下载\python_scripts && python trae_daily_saver.py --scan-key
```
(This requires Trae CN to be running)

### Step 2: Generate AI daily report from the dialogue

Read the generated dialogue file and use your own AI capabilities to summarize it into a daily report.

Requirements for the report:
- Length: approximately 200 Chinese characters
- Focus on technical optimization, debugging, and development discussions
- Clearly describe task progress and results for each item
- Use formal workplace Chinese tone
- Avoid all English identifiers, code snippets, function names, etc.
- Only describe actual work activities and business progress
- Structure: complete and concise

### Step 3: Return report to user

Display the generated daily report in the conversation. Also save it to `d:\下载\daily_report_YYYYMMDD.md`.

Format:
```markdown
## YYYY年MM月DD日 工作日报

(Report content here)
```
