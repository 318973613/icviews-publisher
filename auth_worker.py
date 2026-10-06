"""One auth operation per process; private inputs arrive through stdin, never argv."""
import json
import sys

import publish as publisher


def handle(operation, payload):
    try:
        if not isinstance(payload, dict):
            raise publisher.PublishError("登录输入格式不正确。")
        mobile, code = payload.get("mobile"), payload.get("code")
        if not isinstance(mobile, str) or (operation == "login" and not isinstance(code, str)):
            raise publisher.PublishError("手机号或验证码格式不正确。")
        if operation == "sms":
            publisher.send_sms(mobile)
            result = None
        elif operation == "login":
            result = publisher.login_sms(mobile, code)
        else:
            raise publisher.PublishError("不支持的登录操作。")
        return {"ok": True, "result": result}
    except publisher.PublishError as error:
        publisher.record_error(error)
        return {"ok": False, "message": str(error)}
    except Exception:
        return {"ok": False, "message": "登录处理发生内部错误，未确认操作成功。"}


def main():
    try:
        payload = json.loads(sys.stdin.read(4096))
        result = handle(sys.argv[1] if len(sys.argv) == 2 else "", payload)
    except Exception:
        result = {"ok": False, "message": "无法读取登录输入，未发送请求。"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
