class ServiceUnavailableError(RuntimeError):
    """依赖服务失败，调用方应提示重试，不创建人工待办。"""
