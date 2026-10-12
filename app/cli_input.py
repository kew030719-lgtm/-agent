import sys


def read_input(prompt: str) -> str:
    """读取终端原始字节，兼容 UTF-8 和 GB18030 输入。"""
    sys.stdout.write(prompt)
    sys.stdout.flush()

    raw = sys.stdin.buffer.readline()
    if not raw:
        raise EOFError
    raw = raw.rstrip(b"\r\n")

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "终端输入无法按 UTF-8 或 GB18030 解码，请将终端编码设为 UTF-8"
            ) from exc
