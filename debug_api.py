#!/usr/bin/env python3
"""
Polymarket API 调试脚本
用于生成curl命令和测试API凭证获取
"""

import os
import time
import json
import hmac
import hashlib
import base64
from dotenv import load_dotenv

load_dotenv()

def generate_curl_commands():
    """生成用于调试的curl命令"""
    
    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    
    if not private_key:
        print("❌ 错误: 环境变量 POLY_PRIVATE_KEY 未设置")
        print("请在 .env 文件中设置 POLY_PRIVATE_KEY")
        return
    
    # 确保私钥格式正确
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        
        account = Account.from_key(private_key)
        wallet_address = account.address
        
        print("=" * 60)
        print("Polymarket API 调试信息")
        print("=" * 60)
        print(f"\n钱包地址: {wallet_address}")
        print(f"私钥 (前10位): {private_key[:12]}...")
        
        # 生成签名
        nonce = int(time.time() * 1000)
        timestamp = int(time.time())
        message = f"I want to create a new API key on Polymarket CLOB with nonce {nonce}"
        
        message_hash = encode_defunct(text=message)
        signed = account.sign_message(message_hash)
        signature = "0x" + signed.signature.hex()
        
        request_body = {
            "message": message,
            "signature": signature,
            "nonce": nonce,
            "timestamp": timestamp
        }
        
        print("\n" + "=" * 60)
        print("1. 派生 API 凭证 (derive-api-key)")
        print("=" * 60)
        print(f"\n消息: {message}")
        print(f"签名: {signature[:30]}...{signature[-20:]}")
        print(f"Nonce: {nonce}")
        print(f"Timestamp: {timestamp}")
        
        # 生成curl命令
        curl_cmd = f'''curl -X POST "https://clob.polymarket.com/auth/derive-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{json.dumps(request_body)}'
'''
        print("\n📋 curl 命令:")
        print("-" * 40)
        print(curl_cmd)
        
        # 测试API
        print("\n" + "=" * 60)
        print("2. 执行 API 请求测试")
        print("=" * 60)
        
        import httpx
        
        print("\n正在请求 derive-api-key...")
        response = httpx.post(
            "https://clob.polymarket.com/auth/derive-api-key",
            json=request_body,
            timeout=30.0
        )
        
        print(f"状态码: {response.status_code}")
        print(f"响应: {response.text[:500] if response.text else '(空)'}")
        
        if response.status_code == 200:
            data = response.json()
            api_key = data.get("apiKey")
            api_secret = data.get("secret")
            api_passphrase = data.get("passphrase")
            
            print("\n✅ API凭证获取成功!")
            print(f"  API Key: {api_key[:15]}..." if api_key else "  API Key: 无")
            print(f"  Passphrase: {api_passphrase}" if api_passphrase else "  Passphrase: 无")
            
            # 生成余额查询的curl命令
            if api_key and api_secret and api_passphrase:
                print("\n" + "=" * 60)
                print("3. 查询代理钱包余额")
                print("=" * 60)
                
                # 生成认证头
                ts = str(int(time.time()))
                path = "/balance"
                sig_message = f"{ts}GET{path}"
                sig = hmac.new(
                    base64.b64decode(api_secret),
                    sig_message.encode(),
                    hashlib.sha256
                ).digest()
                sig_b64 = base64.b64encode(sig).decode()
                
                balance_curl = f'''curl -X GET "https://clob.polymarket.com/balance?address={wallet_address}" \\
  -H "POLY_ADDRESS: {wallet_address}" \\
  -H "POLY_SIGNATURE: {sig_b64}" \\
  -H "POLY_TIMESTAMP: {ts}" \\
  -H "POLY_API_KEY: {api_key}" \\
  -H "POLY_PASSPHRASE: {api_passphrase}"
'''
                print("\n📋 余额查询 curl 命令:")
                print("-" * 40)
                print(balance_curl)
                
                # 实际查询余额
                print("\n正在查询余额...")
                balance_response = httpx.get(
                    f"https://clob.polymarket.com/balance",
                    params={"address": wallet_address},
                    headers={
                        "POLY_ADDRESS": wallet_address,
                        "POLY_SIGNATURE": sig_b64,
                        "POLY_TIMESTAMP": ts,
                        "POLY_API_KEY": api_key,
                        "POLY_PASSPHRASE": api_passphrase
                    },
                    timeout=30.0
                )
                
                print(f"状态码: {balance_response.status_code}")
                print(f"响应: {balance_response.text}")
                
                if balance_response.status_code == 200:
                    balance_data = balance_response.json()
                    print(f"\n💰 代理钱包余额:")
                    print(f"  可用: {balance_data.get('available', 0)} USDC")
                    print(f"  锁定: {balance_data.get('locked', 0)} USDC")
        else:
            print("\n❌ API凭证获取失败!")
            print("可能的原因:")
            print("  1. 私钥格式错误")
            print("  2. 钱包地址未在 Polymarket 注册")
            print("  3. 网络问题")
            print("  4. API 端点变更")
            
    except ImportError as e:
        print(f"❌ 缺少依赖: {e}")
        print("请运行: pip install eth-account httpx python-dotenv")
    except Exception as e:
        import traceback
        print(f"❌ 错误: {e}")
        print(traceback.format_exc())


def check_env():
    """检查环境变量"""
    print("=" * 60)
    print("环境变量检查")
    print("=" * 60)
    
    env_vars = [
        "POLY_PRIVATE_KEY",
        "POLY_API_KEY", 
        "POLY_API_SECRET",
        "POLY_API_PASSPHRASE"
    ]
    
    for var in env_vars:
        value = os.getenv(var, "")
        if value:
            # 只显示前几个字符
            display = value[:10] + "..." if len(value) > 10 else value
            print(f"  {var}: ✅ 已设置 ({display})")
        else:
            print(f"  {var}: ❌ 未设置")


if __name__ == "__main__":
    print("\n" + "🔧 " * 20 + "\n")
    check_env()
    print()
    generate_curl_commands()
    print("\n" + "🔧 " * 20 + "\n")
