"""复用 openJiuwen 的模型客户端，并保留原始 token 缺测语义。"""
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

from .model_config import DeepSeekSettings


class EvidenceDeepSeekClient(OpenAIModelClient):
    # SDK 的公开客户端注册扩展；不修改已安装的 SDK 或共享 schema。
    __client_name__ = "assurance_deepseek"

    async def _parse_response(self, response, parser=None):
        message = await super()._parse_response(response, parser)
        usage = getattr(response, "usage", None)
        counts = [getattr(usage, name, None) for name in ("prompt_tokens", "completion_tokens")]
        # 0.1.18 会把缺少的原始计数填成 0；账本不能把缺测当作真实零值。
        if not all(isinstance(count, int) and not isinstance(count, bool) and count >= 0 for count in counts):
            message.usage_metadata = None
        elif message.usage_metadata is not None:
            message.usage_metadata.model_name = getattr(response, "model", None) or self.model_config.model_name
        return message


def build_deepseek_model(settings: DeepSeekSettings) -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="assurance_deepseek", api_mode="chat_completions",
            api_key=settings.api_key, api_base=settings.base_url,
            timeout=settings.timeout_seconds, max_retries=0,
            use_shared_llm_http_client=False,
        ),
        model_config=ModelRequestConfig(
            model_name=settings.model, max_tokens=settings.max_tokens,
            # 自定义客户端名不能套用 SDK 的 OpenAI reasoning_effort=none；
            # 显式使用 DeepSeek Chat Completions 的关闭思考参数。
            extra_body={"thinking": {"type": "disabled"}},
        ),
    )


def explain_live_error(exc: Exception) -> dict:
    """不回显服务端响应正文或完整异常，避免凭证被包含在错误信息中。"""
    current = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(current, "status_code", None)
        hints = {
            400: "模型请求被拒绝，请检查模型名称和接口参数。",
            401: "API Key 验证失败，请检查本地密钥是否有效。",
            402: "模型账户余额不足，请到 DeepSeek 开放平台检查余额。",
            403: "模型访问被拒绝，请检查账户权限。",
            429: "请求受到限流，请稍后手动重试。",
        }
        if status in hints:
            return {"type": "model_http_error", "http_status": status, "message": hints[status]}
        if "Timeout" in type(current).__name__:
            return {"type": "model_timeout", "message": "模型请求超时，请检查网络或稍后手动重试。"}
        if "Connection" in type(current).__name__:
            return {"type": "model_connection_error", "message": "无法连接模型服务，请检查本机网络或代理。"}
        current = current.__cause__ or current.__context__
    return {"type": type(exc).__name__, "message": "真实模型验证未完成，请检查调用事件和模型输出格式。"}
