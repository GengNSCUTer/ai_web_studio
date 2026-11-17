"""模型只能提出待批准的代码；查询结果同样限制到当前用户与项目。"""

import json

from app.schemas.plugins import SandboxCodePreviewCreate
from app.services.sandbox_service import SandboxService
from app.services.plugin_service import PluginError
from app.services.tools.schemas import ExternalSource


class SandboxPythonProvider:
    def __init__(self, *, db, user_id, project_id):
        self.db, self.user_id, self.project_id = db, user_id, project_id

    def service(self):
        if not self.db or not self.user_id or not self.project_id:
            raise PluginError('通用代码执行需要当前用户和工作区。')
        return SandboxService(self.db)

    def preview(self, arguments):
        return self.service().preview_code(self.user_id, SandboxCodePreviewCreate(
            project_id=self.project_id, code=arguments['code'], file_ids=arguments.get('file_ids', [])))

    async def run(self, call):
        service = self.service()
        row = service.get(self.user_id, call.arguments['job_id'])
        if row.project_id != self.project_id:
            raise PluginError('任务不属于当前工作区。')
        result = json.loads(row.result_json) if row.result_json else {}
        text = f'沙箱任务状态：{row.status}。'
        if row.status == 'succeeded':
            text += '\n' + str(result.get('stdout', ''))[:4096]
            for artifact in result.get('artifacts', [])[:8]:
                text += f"\n{artifact['name']}：{artifact['content'][:2000]}"
        elif row.error:
            text += '\n' + row.error
        else:
            text += ' 尚无完成结果，不代表业务已完成。'
        source = ExternalSource(source_type='sandbox_result', provider='sandbox', title='Python隔离执行结果',
            display_text=text, metadata={'job_id': row.id, 'status': row.status, 'tool_key': call.tool_key})
        return [source], {'job_status': row.status}
