"""
Polymarket 签名配置测试脚本
用于诊断和验证邮箱类型账户的签名配置
"""

import os
import sys

def load_env_file():
    """手动加载 .env 文件"""
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

# 加载环境变量
load_env_file()

def test_env_variables():
    """测试环境变量配置"""
    print("=" * 60)
    print("步骤 1: 检查环境变量")
    print("=" * 60)

    private_key = os.getenv("POLY_PRIVATE_KEY")
    funder = os.getenv("POLY_FUNDER")

    issues = []

    # 检查私钥
    if not private_key:
        print("❌ POLY_PRIVATE_KEY 未设置")
        issues.append("POLY_PRIVATE_KEY")
    else:
        if private_key.startswith("0x"):
            print(f"✅ POLY_PRIVATE_KEY 已设置: {private_key[:10]}...{private_key[-6:]}")
        else:
            print(f"⚠️  POLY_PRIVATE_KEY 已设置但缺少 0x 前缀: {private_key[:8]}...")
            issues.append("POLY_PRIVATE_KEY (缺少0x前缀)")

    # 检查 funder（关键！）
    if not funder:
        print("❌ POLY_FUNDER 未设置 - 这是导致签名错误的主要原因！")
        issues.append("POLY_FUNDER")
    else:
        if funder.startswith("0x"):
            print(f"✅ POLY_FUNDER 已设置: {funder[:10]}...{funder[-6:]}")
        else:
            print(f"⚠️  POLY_FUNDER 已设置但缺少 0x 前缀: {funder[:8]}...")
            issues.append("POLY_FUNDER (缺少0x前缀)")

    print()

    if issues:
        print("🔧 需要修复的配置项:")
        for issue in issues:
            print(f"   - {issue}")
        return False
    else:
        print("✅ 所有环境变量配置正确")
        return True

def test_clob_client():
    """测试 CLOB 客户端初始化和签名"""
    print("\n" + "=" * 60)
    print("步骤 2: 测试 CLOB 客户端")
    print("=" * 60)

    private_key = os.getenv("POLY_PRIVATE_KEY")
    funder = os.getenv("POLY_FUNDER")

    if not private_key or not funder:
        print("❌ 跳过测试（环境变量未完整配置）")
        return False

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        # 创建客户端
        print("正在创建 CLOB 客户端...")
        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            signature_type=1,  # Email/Magic wallet
            funder=funder      # 必需！
        )
        print("✅ CLOB 客户端创建成功")

        # 创建 API 凭证
        print("\n正在创建/派生 API 凭证...")
        creds = client.create_or_derive_api_creds()
        if creds:
            client.set_api_creds(creds)
            print("✅ API 凭证创建成功")
        else:
            print("❌ API 凭证创建失败")
            return False

        # 测试签名（通过查询余额）
        print("\n正在测试签名（查询余额）...")
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        balance = client.get_balance_allowance(params=params)

        if balance:
            # 转换余额
            balance_usdc = float(balance.get("balance", 0)) / 1_000_000
            print(f"✅ 签名验证成功！账户余额: ${balance_usdc:.2f} USDC")
            return True
        else:
            print("❌ 查询余额失败")
            return False

    except ImportError:
        print("❌ py_clob_client 未安装")
        print("   运行: pip install py-clob-client")
        return False
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def print_help():
    """打印帮助信息"""
    print("\n" + "=" * 60)
    print("📖 如何获取 POLY_FUNDER 地址")
    print("=" * 60)
    print("""
对于邮箱登录的 Polymarket 账户，你需要：

1. 获取 Polymarket Proxy 地址（这就是 POLY_FUNDER）：

   方法1 - 从 Polymarket 网站：
   a. 登录 https://polymarket.com
   b. 点击右上角账户图标 → Settings
   c. 在 Wallet 部分找到你的地址（通常以 0x 开头）
   d. 这就是你的 POLY_FUNDER 地址

   方法2 - 从区块链浏览器：
   a. 访问 https://polygonscan.com
   b. 搜索你的任意一笔 Polymarket 交易
   c. 查看 "From" 地址，这就是你的 Proxy 地址

2. 获取 Magic 私钥（POLY_PRIVATE_KEY）：

   a. 访问 https://reveal.magic.link/polymarket
   b. 使用邮箱登录（与 Polymarket 相同的邮箱）
   c. 复制显示的私钥（包含 0x 前缀）

3. 配置环境变量：

   在项目根目录创建或编辑 .env 文件：

   POLY_PRIVATE_KEY=0x你的Magic私钥
   POLY_FUNDER=0x你的Polymarket_Proxy地址

   TG_BOT_TOKEN=你的Telegram_Bot_Token (可选)
   TG_CHAT_ID=你的Telegram_Chat_ID (可选)

4. 重启应用测试：

   python test_signature.py
   python run.py
""")

def main():
    print("\n🔍 Polymarket 签名配置诊断工具\n")

    # 测试环境变量
    env_ok = test_env_variables()

    # 测试 CLOB 客户端
    if env_ok:
        client_ok = test_clob_client()
    else:
        client_ok = False

    # 打印总结
    print("\n" + "=" * 60)
    print("📊 测试总结")
    print("=" * 60)

    if env_ok and client_ok:
        print("✅ 所有测试通过！你的配置正确，可以开始交易了。")
        print("\n下一步: 运行 python run.py 启动应用")
    else:
        print("❌ 存在配置问题，请按照下方指南修复。")
        print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
