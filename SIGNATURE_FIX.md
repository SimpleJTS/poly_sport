# 🔧 Polymarket 签名错误修复指南

## 问题诊断

你遇到的 `invalid signature` 错误是因为**邮箱类型账户缺少 POLY_FUNDER 配置**。

对于 `signature_type=1`（Email/Magic wallet），`funder` 参数是**必需的**！

## 快速修复步骤

### 步骤 1: 获取 Polymarket Proxy 地址（POLY_FUNDER）

**方法 1 - 从 Polymarket 网站获取（推荐）：**

1. 访问 https://polymarket.com 并登录
2. 点击右上角的账户图标
3. 选择 "Settings" 或 "Account Settings"
4. 找到 "Wallet Address" 或 "Proxy Wallet"
5. 复制这个地址（格式：`0x1234...abcd`）

**方法 2 - 从区块链浏览器获取：**

1. 访问 https://polygonscan.com
2. 搜索你的任意一笔 Polymarket 交易哈希
3. 在交易详情中找到 "From" 地址
4. 这就是你的 Polymarket Proxy 地址

### 步骤 2: 确认你的 Magic 私钥（POLY_PRIVATE_KEY）

1. 访问 https://reveal.magic.link/polymarket
2. 使用你的 Polymarket 邮箱登录
3. 复制显示的私钥（应该包含 `0x` 前缀）

### 步骤 3: 配置环境变量

在项目根目录 `/home/user/poly_sport/` 创建或编辑 `.env` 文件：

```bash
# Polymarket 配置（必需）
POLY_PRIVATE_KEY=0x你的Magic私钥
POLY_FUNDER=0x你的Polymarket_Proxy地址

# Telegram 通知（可选）
TG_BOT_TOKEN=你的Telegram_Bot_Token
TG_CHAT_ID=你的Telegram_Chat_ID
```

**重要提示：**
- 两个地址都必须以 `0x` 开头
- 不要添加引号
- 不要有空格

### 步骤 4: 验证配置

运行测试脚本验证配置：

```bash
cd /home/user/poly_sport
python test_signature.py
```

测试脚本会：
- ✅ 检查环境变量是否正确设置
- ✅ 测试 CLOB 客户端初始化
- ✅ 验证签名（通过查询余额）
- 📊 提供详细的诊断信息

### 步骤 5: 启动应用

配置验证通过后，启动应用：

```bash
python run.py
```

## 技术细节

### 为什么需要 POLY_FUNDER？

对于邮箱登录的账户：

1. **Magic 私钥** → 用于签名交易
2. **Polymarket Proxy** → 实际发送交易的合约地址

ClobClient 需要这两个参数才能正确构建和签名订单：

```python
client = ClobClient(
    "https://clob.polymarket.com",
    key=POLY_PRIVATE_KEY,      # Magic 私钥
    chain_id=137,
    signature_type=1,          # Email/Magic wallet
    funder=POLY_FUNDER         # Proxy 地址（必需！）
)
```

### 签名类型说明

| signature_type | 用途 | 需要 funder |
|----------------|------|-------------|
| 0 | EOA (MetaMask等标准钱包) | ❌ 否 |
| 1 | **Email/Magic wallet** | ✅ **是** |
| 2 | Gnosis Safe/Proxy | ✅ 是 |

## 常见问题

### Q: 我找不到 Proxy 地址怎么办？

A: 在 Polymarket 网站进行一笔小额交易（如 $1），然后：
1. 在交易确认页面复制交易哈希
2. 在 PolygonScan 查询这个交易
3. "From" 地址就是你的 Proxy 地址

### Q: 测试脚本显示 "API 凭证创建失败"？

A: 检查：
1. 私钥是否正确（从 reveal.magic.link 获取）
2. Funder 地址是否正确（从 Polymarket 设置获取）
3. 网络连接是否正常

### Q: 我的 .env 文件在哪里？

A: 在项目根目录：

```bash
cd /home/user/poly_sport
ls -la .env
```

如果不存在，创建它：

```bash
cat > .env << 'ENVEOF'
POLY_PRIVATE_KEY=0x你的私钥
POLY_FUNDER=0x你的Proxy地址
ENVEOF
```

## 参考资料

- [Polymarket 官方文档 - Authentication](https://docs.polymarket.com/developers/CLOB/authentication)
- [Polymarket 官方文档 - Proxy Wallet](https://docs.polymarket.com/developers/proxy-wallet)
- [如何导出私钥](https://docs.polymarket.com/polymarket-learn/FAQ/how-to-export-private-key)
- [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client)

## 需要帮助？

如果按照上述步骤仍无法解决，请：

1. 运行 `python test_signature.py` 并复制完整输出
2. 检查是否有防火墙或网络限制
3. 确认账户中有足够的 USDC 余额

