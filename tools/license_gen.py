"""作者授权码签发工具（仅作者/分发者使用）。

用法示例：
  # 给某台机器签发 1 年商业授权（machine 绑定该机 install_id）
  python tools/license_gen.py --install-id INST-XXXXXXXXXXXXXXXX --owner "某某公司" --days 365 --out license.key

  # 签发永久授权（通用，不绑机）
  python tools/license_gen.py --any --type perpetual --owner "VIP 客户" --out license.key

  # 签发 30 天试用授权
  python tools/license_gen.py --any --type trial --days 30 --out trial.key

输出为单行授权码，整段发给客户，客户在「授权激活」页面粘贴即可。
注：install_id 可在目标机器的「关于与授权」页或管理控制台查到（install_id 字段）。
"""
import argparse
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "app")
from core.licensing import make_token


def main():
    ap = argparse.ArgumentParser(description="Felix 授权码签发")
    ap.add_argument("--install-id", help="目标机器 install_id（绑定单台设备）")
    ap.add_argument("--any", action="store_true", help="通用授权，不绑定设备")
    ap.add_argument("--type", default="commercial",
                    choices=["commercial", "perpetual", "trial"], help="授权类型")
    ap.add_argument("--owner", default="授权用户", help="授权归属方名称")
    ap.add_argument("--days", type=int, default=365, help="有效期天数（perpetual 忽略）")
    ap.add_argument("--features", default="", help="可选功能标签，逗号分隔")
    ap.add_argument("--out", help="输出到文件（否则打印到终端）")
    args = ap.parse_args()

    if not args.any and not args.install_id:
        ap.error("必须指定 --install-id（绑定设备）或 --any（通用）")
    machine = "ANY" if args.any else args.install_id
    expires = None
    if args.type != "perpetual":
        expires = (datetime.now() + timedelta(days=args.days)).strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "v": 1,
        "type": args.type,
        "machine": machine,
        "owner": args.owner,
        "issued": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "expires": expires,
        "features": [x.strip() for x in args.features.split(",") if x.strip()],
    }
    token = make_token(payload)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(token + "\n")
        print(f"已写入授权码 -> {args.out}")
    else:
        print(token)
    print(f"\n类型={args.type}  绑定={machine}  归属={args.owner}  到期={expires or '永久'}")


if __name__ == "__main__":
    main()
