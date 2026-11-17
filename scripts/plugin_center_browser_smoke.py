"""固定账号真实浏览器验收；验证安装授权与不可用阻断，不冒充容器执行。"""

import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select, delete
from app.core.database import SessionLocal
from app.models.user import User
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.plugin import PluginInstallation, PluginRelease, ProjectPlugin, SandboxJob, PluginSubmission
from app.models.tool_config import UserSkillInstallation, SkillInstallationRevision
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.tool_trace import ToolCallRun, ToolRouteRun
from app.models.user_setting import UserSetting
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService


def run():
    key = "knowledge.organize"
    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "1528713326@qq.com"))
        if not user:
            raise RuntimeError("固定账号不存在")
        uid = user.id
        token = AuthService(UserRepository(db)).create_access_token(user)
        old_installation = db.scalar(select(PluginInstallation).where(PluginInstallation.user_id == uid, PluginInstallation.plugin_key == key))
        old_installation_values = {c.name: getattr(old_installation, c.name) for c in PluginInstallation.__table__.columns} if old_installation else None
        old_releases = set(db.scalars(select(PluginRelease.id).where(PluginRelease.user_id == uid, PluginRelease.plugin_key == key)))
        skill = db.scalar(select(UserSkillInstallation).where(UserSkillInstallation.user_id == uid, UserSkillInstallation.skill_key == "workspace.document-review"))
        old_skill = {c.name: getattr(skill, c.name) for c in UserSkillInstallation.__table__.columns} if skill else None
        old_skill_revisions = set(db.scalars(select(SkillInstallationRevision.id).where(SkillInstallationRevision.user_id == uid, SkillInstallationRevision.skill_key == "workspace.document-review")))
        project = Project(user_id=uid, name=f"plugin-smoke-{suffix}")
        db.add(project); db.flush()
        pid = project.id
        db.add(ProjectFile(project_id=pid, user_id=uid, kind="text", file_name="插件测试资料.md",
                           parsed_text="# 测试资料\n仅本次授权副本；原文件不应被修改。", storage_key=f"{uid}/test-only-{suffix}"))
        db.commit()
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": "http://127.0.0.1:32008", "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.goto("http://127.0.0.1:32008/plugins", wait_until="networkidle")
            expect(page.get_by_role("heading", name="审核插件中心", exact=True)).to_be_visible()
            expect(page.locator("article")).to_have_count(4)
            page.get_by_label("当前工作区").select_option(pid)
            card = page.locator("article").filter(has=page.get_by_role("heading", name="知识整理", exact=True))
            card.get_by_role("button", name="安装预检", exact=True).click()
            expect(page.get_by_role("button", name="确认安装审核版本")).to_be_visible()
            page.get_by_role("button", name="确认安装审核版本").click()
            expect(card.get_by_role("button", name="在项目启用")).to_be_enabled()
            card.get_by_role("button", name="在项目启用").click()
            expect(card.get_by_role("button", name="停用项目")).to_be_visible()
            page.get_by_label("插件测试资料.md", exact=True).check()
            page.get_by_role("button", name="预览本次脚本授权", exact=True).click()
            expect(page.get_by_text("任务状态：awaiting_approval", exact=True)).to_be_visible()
            bwrap_smoke=os.getenv('AIWS_PLUGIN_BWRAP_SMOKE')=='1'
            if bwrap_smoke:
                expect(page.get_by_role('button',name='批准本次隔离执行')).to_be_enabled()
            else:
                expect(page.get_by_role("button", name="批准本次隔离执行")).to_be_disabled()
            expect(page.get_by_text("授权副本：插件测试资料.md", exact=True)).to_be_visible()
            page.screenshot(path="/tmp/aiws-plugin-center-desktop.png", full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path="/tmp/aiws-plugin-center-mobile.png", full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "移动端横向溢出"
            # 即使绕过禁用按钮直接请求，服务端仍拒绝，不创建执行队列。
            with SessionLocal() as db:
                job = db.scalar(select(SandboxJob).where(SandboxJob.project_id == pid))
                job_id = job.id
            if bwrap_smoke:
                page.get_by_role('button',name='批准本次隔离执行').click()
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    result=context.request.get(f'http://127.0.0.1:32008/api/backend/plugins/sandbox/jobs/{job_id}').json()
                    if result['status'] in {'succeeded','failed','cancelled'}:break
                    time.sleep(.2)
                assert result['status']=='succeeded',result.get('error')
                assert len(result['result']['artifacts'])==2
                page.get_by_role('button',name='刷新任务',exact=True).click()
                expect(page.get_by_text('任务状态：succeeded',exact=True)).to_be_visible()
                saved=context.request.post(f'http://127.0.0.1:32008/api/backend/plugins/sandbox/jobs/{job_id}/artifacts/1/save')
                assert saved.status==201
                download=context.request.get('http://127.0.0.1:32008'+saved.json()['download_url'].replace('/api/','/api/backend/',1))
                assert download.status==200 and '插件测试资料.md' in download.text()
                with SessionLocal() as db:
                    original=db.scalar(select(ProjectFile).where(ProjectFile.project_id==pid,ProjectFile.file_name=='插件测试资料.md'))
                    assert original.parsed_text=='# 测试资料\n仅本次授权副本；原文件不应被修改。'
                page.screenshot(path='/tmp/aiws-bwrap-browser.png',full_page=True)
                page.get_by_role('button',name='预览本次脚本授权',exact=True).click()
                expect(page.get_by_text('任务状态：awaiting_approval',exact=True)).to_be_visible()
                page.get_by_role('button',name='取消任务',exact=True).click()
                expect(page.get_by_text('任务状态：cancelled',exact=True)).to_be_visible()
            else:
                response = context.request.post(f"http://127.0.0.1:32008/api/backend/plugins/sandbox/{job_id}/confirm", data={"approved": True})
                assert response.status == 400, response.status
                with SessionLocal() as db:
                    assert db.get(SandboxJob, job_id).status == "awaiting_approval"
            online_chat = "not_requested"
            python_smoke = 'not_requested'
            if os.getenv('AIWS_PLUGIN_PYTHON_SMOKE') == '1':
                # 两个业务只换代码，不修改后端脚本注册表。
                for source, expected_text in (
                    ('import statistics\nprint(statistics.mean([2,4,6]))\nemit_artifact("mean.json", "{\\"mean\\":4}")', '4'),
                    ('from collections import Counter\nprint(Counter(["好","差","好"])["好"])\nemit_artifact("feedback.md", "# 反馈统计\\n好评：2")', '2'),
                ):
                    page.get_by_label('本次 Python 代码', exact=True).fill(source)
                    with page.expect_response(lambda r: '/sandbox/python/preview' in r.url and r.request.method=='POST') as response:
                        page.get_by_role('button',name='预览通用 Python 授权',exact=True).click()
                    preview = response.value.json()
                    assert preview['status']=='awaiting_approval' and preview['code']==source
                    page.get_by_role('button',name='批准本次隔离执行',exact=True).click()
                    deadline=time.monotonic()+30
                    while time.monotonic()<deadline:
                        result=context.request.get(f'http://127.0.0.1:32008/api/backend/plugins/sandbox/jobs/{preview["id"]}').json()
                        if result['status'] in {'succeeded','failed','cancelled'}:break
                        time.sleep(.2)
                    assert result['status']=='succeeded',result.get('error')
                    assert result['result']['stdout'].strip()==expected_text
                    assert len(result['result']['artifacts'])==1
                    page.get_by_role('button',name='刷新任务',exact=True).click()
                    expect(page.get_by_text('任务状态：succeeded',exact=True)).to_be_visible()
                page.screenshot(path='/tmp/aiws-python-sandbox-browser.png',full_page=True)
                python_smoke='two_programs_approved_and_executed'
            if os.getenv('AIWS_PLUGIN_PYTHON_CHAT_SMOKE') == '1':
                with SessionLocal() as db:
                    setting=db.scalar(select(UserSetting).where(UserSetting.user_id==uid))
                    assert setting.provider_type=='openai-compatible'
                    model=setting.default_model
                created=context.request.post('http://127.0.0.1:32008/api/backend/conversations',data={
                    'title':'Python沙箱模型验收','project_id':pid,**({'model_name':model} if model else {})})
                assert created.status==201
                cid=created.json()['id']
                started=context.request.post('http://127.0.0.1:32008/api/backend/chat/runs',data={
                    'conversation_id':cid,'content':'请调用sandbox.python.execute准备Python标准库代码，计算[2,4,6]的平均值并print输出；只准备执行请求，等我在界面批准，不要联网，不要修改文件。','tool_run_mode':'quick_chat','web_search_enabled':True})
                assert started.status==202
                chat_id=started.json()['id']
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    snapshot=context.request.get(f'http://127.0.0.1:32008/api/backend/chat/runs/{chat_id}').json()
                    if snapshot['status'] in {'succeeded','failed','cancelled','expired','interrupted'}:break
                    time.sleep(.5)
                assert snapshot['status']=='succeeded',snapshot.get('error_code')
                with SessionLocal() as db:
                    pending=db.scalar(select(SandboxJob).where(SandboxJob.project_id==pid,
                        SandboxJob.script_key=='python_stdlib',SandboxJob.status=='awaiting_approval').order_by(SandboxJob.created_at.desc()))
                    if not pending:
                        for route in db.scalars(select(ToolRouteRun).where(ToolRouteRun.conversation_id==cid)):
                            print(json.dumps({c.name:getattr(route,c.name) for c in ToolRouteRun.__table__.columns if c.name in {
                                'status','planner','plan_json','events_json','selected_tools_json','query','error_message'}},ensure_ascii=False),flush=True)
                        print(json.dumps({'synthetic_answer':snapshot['content']},ensure_ascii=False),flush=True)
                    assert pending,'模型没有真实创建待审批任务'
                    model_job=pending.id
                page.goto(f'http://127.0.0.1:32008/plugins?job={model_job}',wait_until='networkidle')
                expect(page.get_by_text('待执行代码（确认后不能替换）',exact=True)).to_be_visible()
                page.get_by_role('button',name='批准本次隔离执行',exact=True).click()
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    result=context.request.get(f'http://127.0.0.1:32008/api/backend/plugins/sandbox/jobs/{model_job}').json()
                    if result['status'] in {'succeeded','failed','cancelled'}:break
                    time.sleep(.2)
                assert result['status']=='succeeded' and '4' in result['result']['stdout'],result.get('error')
                followup=context.request.post('http://127.0.0.1:32008/api/backend/chat/runs',data={
                    'conversation_id':cid,'content':f'请调用sandbox.jobs.get查询沙箱任务{model_job}的真实结果，再说明平均值。不要创建新任务或新代码。','tool_run_mode':'quick_chat','web_search_enabled':True})
                assert followup.status==202
                followup_id=followup.json()['id']
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    answer=context.request.get(f'http://127.0.0.1:32008/api/backend/chat/runs/{followup_id}').json()
                    if answer['status'] in {'succeeded','failed','cancelled','expired','interrupted'}:break
                    time.sleep(.5)
                assert answer['status']=='succeeded' and '4' in answer['content'],'未根据已执行结果生成最终回答'
                with SessionLocal() as db:
                    route_ids=list(db.scalars(select(ToolRouteRun.id).where(ToolRouteRun.conversation_id==cid)))
                    calls=list(db.scalars(select(ToolCallRun.tool_key).where(ToolCallRun.route_run_id.in_(route_ids))))
                    assert 'sandbox.jobs.get' in calls,'没有真实查询执行结果'
                python_smoke += '_online_planner_to_browser_approval_to_worker'
            if os.getenv("AIWS_PLUGIN_CHAT_SMOKE") == "1":
                with SessionLocal() as db:
                    setting = db.scalar(select(UserSetting).where(UserSetting.user_id == uid))
                    assert setting.provider_type == "openai-compatible", "只使用账号在线 API，不加载本地模型"
                page.goto("http://127.0.0.1:32008/chat", wait_until="networkidle")
                page.locator("select").nth(0).select_option(pid)
                page.get_by_role("button", name="新对话", exact=True).click()
                page.get_by_test_id("tool-run-mode-selector").select_option("workspace_review")
                page.get_by_test_id("skill-selector").click()
                page.get_by_test_id("skill-option-workspace.document-review").click()
                page.get_by_placeholder("输入你的问题").fill("请搜索并读取当前工作区的 插件测试资料.md，只逐字引用前两行；不要联网、不要修改。")
                page.get_by_role("button", name="发送消息", exact=True).click()
                page.wait_for_function("() => document.querySelector('button[type=submit]')?.disabled === true", timeout=10000)
                page.wait_for_function("() => ['发送消息','Send'].includes(document.querySelector('button[type=submit]')?.textContent?.trim())", timeout=240000)
                with SessionLocal() as db:
                    conversation = db.scalar(select(Conversation).where(Conversation.project_id == pid))
                    answer = db.scalar(select(Message).where(Message.conversation_id == conversation.id, Message.role == "assistant").order_by(Message.created_at.desc()))
                    assert answer.status == "done" and "原文件不应被修改" in answer.content, "模型最终回答未正确使用插件文件证据"
                    route_ids = list(db.scalars(select(ToolRouteRun.id).where(ToolRouteRun.conversation_id == conversation.id)))
                    executed = list(db.scalars(select(ToolCallRun.tool_key).where(ToolCallRun.route_run_id.in_(route_ids))))
                    assert "workspace.files.read" in executed, "未真实调用文件读取工具"
                online_chat = "passed_planner_read_answer_persistence"
            assert not errors, errors
            context.close(); browser.close()
        print(json.dumps({"cards": 4, "install": "passed", "project_enable": "passed", "preview": "passed",
                          "missing_runtime_refusal": "not_applicable" if bwrap_smoke else "passed", "mobile_overflow": False, "browser_errors": 0,
                          "online_chat": online_chat,
                          'python_sandbox':python_smoke,
                          "real_container_execution": "bubblewrap_worker_artifacts_saved_downloaded" if bwrap_smoke else "not_run_missing_rootless_prerequisites"}, ensure_ascii=False))
    finally:
        from stage46a_real_e2e import cleanup as cleanup_chat
        cleanup_chat(user_id=uid, project_id=pid)
        with SessionLocal() as db:
            db.execute(delete(SandboxJob).where(SandboxJob.project_id == pid))
            db.execute(delete(ProjectPlugin).where(ProjectPlugin.project_id == pid))
            db.execute(delete(ProjectFile).where(ProjectFile.project_id == pid))
            db.execute(delete(Project).where(Project.id == pid))
            current = db.scalar(select(PluginInstallation).where(PluginInstallation.user_id == uid, PluginInstallation.plugin_key == key))
            if old_installation_values:
                for name, value in old_installation_values.items(): setattr(current, name, value)
            elif current:
                db.delete(current)
            for release in db.scalars(select(PluginRelease).where(PluginRelease.user_id == uid, PluginRelease.plugin_key == key)):
                if release.id not in old_releases: db.delete(release)
            current_skill = db.scalar(select(UserSkillInstallation).where(UserSkillInstallation.user_id == uid, UserSkillInstallation.skill_key == "workspace.document-review"))
            if old_skill:
                for name, value in old_skill.items(): setattr(current_skill, name, value)
            elif current_skill:
                db.delete(current_skill)
            for revision in db.scalars(select(SkillInstallationRevision).where(SkillInstallationRevision.user_id == uid, SkillInstallationRevision.skill_key == "workspace.document-review")):
                if revision.id not in old_skill_revisions: db.delete(revision)
            db.commit()


if __name__ == "__main__":
    run()
