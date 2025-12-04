# Polymarket 签名错误解决方案

## 问题描述

使用邮箱类型登录（Magic wallet）时遇到 "invalid signature" 错误。

## 根本原因

py_clob_client 对于不同类型的钱包需要不同的签名配置：

### 签名类型说明

| signature_type | 钱包类型 | 说明 | 是否需要 funder |
|----------------|----------|------|-----------------|
| **0** (默认) | **EOA** | MetaMask、硬件钱包等直接控制私钥的钱包 | ❌ 不需要 |
| **1** | **Email/Magic wallet** | 邮箱登录（Magic Link） | ✅ **必须设置** |
| **2** | **POLY_GNOSIS_SAFE** | Gnosis Safe 或浏览器钱包代理 | ✅ **必须设置** |

## 关键发现

当前代码使用 `signature_type=1` (邮箱登录) 是**正确的**，但缺少必需的 `funder` 参数！

### funder 参数的作用

- 对于邮箱登录（Magic wallet），Polymarket 会为你创建一个代理合约（Proxy Wallet）
- **signing key**（私钥对应的地址）≠ **funder**（代理合约地址，持有资金）
- 下单时需要同时提供：
  - `key`: 用于签名的私钥
  - `funder`: 持有资金的代理合约地址

## 解决方案

### 步骤 1：获取必需信息

#### 1.1 导出 Magic 钱包私钥

1. 访问：https://reveal.magic.link/polymarket
2. 登录你的 Polymarket 账户（使用邮箱）
3. 复制显示的私钥
4. ⚠️ **注意**：妥善保管，不要分享给任何人

#### 1.2 获取 Polymarket Proxy 地址

方法一：从 Polymarket 网站
1. 登录 https://polymarket.com
2. 点击右上角账户图标
3. 进入 Settings（设置）
4. 找到 "Wallet Address" 或 "Proxy Address"
5. 复制这个地址（格式：0x...）

方法二：从 PolygonScan 查找
1. 登录 Polymarket 并找到你的任意一笔交易
2. 点击交易查看详情
3. 找到合约地址（Contract Address）
4. 这通常就是你的 Proxy 地址

方法三：通过代码获取
```python
from eth_account import Account

# 你的 Magic 私钥
private_key = "0x..."
account = Account.from_key(private_key)

# 这是签名地址，通常不是 funder
print(f"签名地址: {account.address}")

# funder 是 Polymarket 为你创建的代理合约地址
# 需要从网站或区块链浏览器获取
```

### 步骤 2：配置环境变量

创建或编辑 `.env` 文件：

```bash
# Polymarket API配置
POLY_PRIVATE_KEY=0x你从Magic导出的私钥
POLY_FUNDER=0x你的Polymarket_Proxy地址

# Telegram配置（可选）
TG_BOT_TOKEN=your_telegram_bot_token
TG_CHAT_ID=your_telegram_chat_id

# 服务配置
HOST=0.0.0.0
PORT=9000
DEBUG=false
```

**重要提示**：
- `POLY_PRIVATE_KEY` 和 `POLY_FUNDER` 都必须填写
- `POLY_FUNDER` 通常与私钥对应的地址**不同**
- 如果两个地址相同，说明可能配置错误

### 步骤 3：验证配置

运行测试脚本：

```bash
python test_signature.py
```

测试脚本会检查：
1. ✅ 环境变量是否设置
2. ✅ 私钥是否有效
3. ✅ CLOB 客户端是否能正确初始化
4. ✅ API 凭证是否能创建
5. ✅ 签名是否有效（通过查询余额验证）

### 步骤 4：常见错误处理

#### 错误 1: "invalid signature"

**原因**：
- `POLY_FUNDER` 地址不正确
- 私钥与账户类型不匹配
- `signature_type` 设置错误

**解决方案**：
```python
# 邮箱登录配置（正确）
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,          # 从 Magic 导出的私钥
    chain_id=137,
    signature_type=1,         # Email/Magic wallet
    funder=FUNDER            # Polymarket Proxy 地址（必须！）
)
```

#### 错误 2: 余额为 0 或查询失败

**原因**：
- `signature_type` 设置错误
- API 凭证未正确创建

**解决方案**：
```python
# 确保创建 API 凭证
creds = client.create_or_derive_api_creds()
client.set_api_creds(creds)
```

#### 错误 3: "unauthorized" 或 401 错误

**原因**：
- API 凭证无效
- 私钥不正确

**解决方案**：
- 重新导出 Magic 私钥
- 重新创建 API 凭证

## 代码示例

### 完整的邮箱登录配置

```python
import os
from dotenv import load_dotenv
from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, BalanceAllowanceParams, AssetType

# 加载环境变量
load_dotenv()

# 配置参数
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet
PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY")
FUNDER = os.getenv("POLY_FUNDER")

# 验证配置
if not PRIVATE_KEY or not FUNDER:
    raise ValueError("必须设置 POLY_PRIVATE_KEY 和 POLY_FUNDER 环境变量")

# 初始化客户端
client = ClobClient(
    HOST,
    key=PRIVATE_KEY,
    chain_id=CHAIN_ID,
    signature_type=1,  # Email/Magic wallet
    funder=FUNDER      # Polymarket Proxy 地址
)

# 创建 API 凭证
creds = client.create_or_derive_api_creds()
client.set_api_creds(creds)

# 测试连接
print(f"服务器时间: {client.get_server_time()}")

# 查询余额（验证签名）
params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
result = client.get_balance_allowance(params=params)
balance = float(result.get("balance", 0)) / (10 ** 6)
print(f"余额: ${balance:.2f} USDC")

# 下单示例
order_args = OrderArgs(
    token_id="你的token_id",
    price=0.95,        # 价格 0-1
    size=10.0,         # 数量
    side="BUY"
)

# 创建并提交订单
response = client.create_and_post_order(order_args)
print(f"订单已提交: {response}")
```

### 不同钱包类型的配置对比

```python
# 方式 1: 邮箱登录 (Magic wallet)
client = ClobClient(
    host="https://clob.polymarket.com",
    key="从 Magic 导出的私钥",
    chain_id=137,
    signature_type=1,           # Email/Magic
    funder="Polymarket Proxy 地址"  # 必须设置！
)

# 方式 2: MetaMask/硬件钱包 (EOA)
client = ClobClient(
    host="https://clob.polymarket.com",
    key="你的钱包私钥",
    chain_id=137,
    signature_type=0,           # EOA（默认值）
    # 不需要 funder 参数
)

# 方式 3: Gnosis Safe
client = ClobClient(
    host="https://clob.polymarket.com",
    key="Safe 签名私钥",
    chain_id=137,
    signature_type=2,           # POLY_GNOSIS_SAFE
    funder="Gnosis Safe 地址"     # 必须设置！
)
```

## 常见问题 FAQ

### Q1: 如何确认我使用的是哪种钱包类型？

**A**: 查看你在 Polymarket 的登录方式：
- 如果使用**邮箱登录** → signature_type=1
- 如果使用 **MetaMask 连接** → signature_type=0
- 如果使用 **Gnosis Safe** → signature_type=2

### Q2: signing address 和 funder address 有什么区别？

**A**:
- **Signing address**: 私钥对应的地址，用于签名交易
- **Funder address**: 实际持有资金的地址（代理合约地址）
- 对于邮箱登录，这两个地址是**不同的**

### Q3: 我的 funder 地址和 signing 地址相同，正常吗？

**A**:
- 如果使用邮箱登录，**不正常**，说明配置可能有误
- 如果使用 MetaMask（EOA），是正常的，且不需要设置 funder

### Q4: 如何测试配置是否正确？

**A**: 运行 `python test_signature.py`，测试脚本会：
1. 验证环境变量
2. 测试客户端初始化
3. 尝试查询余额（这会验证签名）
4. 提供详细的错误诊断

### Q5: 仍然出现 "invalid signature" 错误怎么办？

**A**: 按以下步骤排查：
1. 确认 `POLY_FUNDER` 是正确的 Polymarket Proxy 地址
2. 确认 `POLY_PRIVATE_KEY` 是从 https://reveal.magic.link/polymarket 导出的
3. 确认 `signature_type=1`
4. 重新创建 API 凭证
5. 检查账户余额是否充足
6. 查看日志中的详细错误信息

## 参考资源

- **Polymarket 官方文档**: https://docs.polymarket.com/
- **py-clob-client GitHub**: https://github.com/Polymarket/py-clob-client
- **Magic 钱包导出**: https://reveal.magic.link/polymarket
- **Polymarket Proxy 合约**: https://polygonscan.com/address/0xaB45c5A4B0c941a2F231C04C3f49182e1A254052

## 相关 Issues

- [Invalid signature when creating orders in negative risk market #79](https://github.com/Polymarket/py-clob-client/issues/79)
- [Allowance - how to set that for a Polymarket Wallet? #93](https://github.com/Polymarket/py-clob-client/issues/93)

## 更新日志

- 2025-12-04: 创建文档，解决邮箱登录签名错误问题
