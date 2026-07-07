"""Headless 版 ServiceContainer.

只注册 mcp + audio 两个插件，跳过 ui / wake_word / shortcuts：
- ui:      CLIViewManager/GUI 需要真终端或 Qt，无人值守测试里会空转/报错
- wake_word: 依赖麦克风与 sherpa 模型，文本注入测试用不到
- shortcuts: 需要 macOS 辅助功能权限

audio 插件仍注册，但配合环境变量 XIAOZHI_DISABLE_AUDIO=1 会退化为 no-op
（不开声卡）。保留它是为了 protocol.set_audio_handler 有对象可挂，且
TTS 音频帧有地方消费（丢弃）。

复用父类 run() 的完整生命周期（tasks / protocol / event handlers /
资源池），只覆盖 _setup_plugins，避免和上游 run() 逻辑漂移。
"""

from src.bootstrap.container import ServiceContainer


class HeadlessContainer(ServiceContainer):
    async def _setup_plugins(self, mode, ctx, cmd) -> None:
        from src.plugins.audio import AudioPlugin
        from src.plugins.mcp import McpPlugin

        audio_plugin = AudioPlugin()
        mcp_plugin = McpPlugin()

        # audio(优先级10) 与 mcp(优先级20) 均无 requires，
        # 不依赖被裁掉的 wake_word/ui/shortcuts，拓扑注入安全。
        self.plugins.register(mcp_plugin, audio_plugin)

        await self.plugins.setup_all(ctx, cmd)
        self._register_cleanup_resources()

        # TTS 音频直连通道（禁用音频时 on_incoming_audio 内部丢弃）
        self.protocol.set_audio_handler(audio_plugin.on_incoming_audio)
