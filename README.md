# AI小通 · 电话坐席辅助 Demo

面向电信热线降套场景的前端演示：客户概览、三档意愿刻度（坚决降套 / 摇摆不定 / 明确保留）、依据原话、AI 建议动作与参考话术，以及通话转写和归档。

## 本地运行

需要 Python 3.9+，不需要安装第三方依赖：

```sh
git clone https://github.com/zhaoxinfeng619-hub/AIxiaotong.git
cd AIxiaotong
python3 frontend/server.py
```

打开 http://127.0.0.1:8765/ ，点击底部「播放演示」。也可直接打开 frontend/index.html 体验静态演示。

- 客户话语逐段到达后，中间建议自动更新，不需要采用或发送。
- 「坐席视图」隐藏演示控制台。
- 支持暂停、单步、自定义转写、会话保存恢复、JSON 导出和本机归档。
- 明确拒绝继续推荐时停止挽留，办理结果始终等待人工和业务回执。

## 数据与接入边界

当前使用本地关键词规则模拟，不代表真实模型理解；客户、套餐与人物头像均为合成演示资料。没有真实电话、ASR、客户发送通道或业务办理能力。浏览器记录只保存在本机，不上传 GitHub。

本地服务预留 Dify Workflow 转发入口。复制 frontend/.env.example 为 frontend/.env，配置 API 地址与密钥后重启服务，通过顶部「本地模拟」检查连接并切换来源。密钥只在本地服务读取，不能提交 .env。工作流需要接收 request_json 并输出 result；实际业务工作流与工具服务不包含在这个前端 Demo 仓库中，真实 Dify 联调尚未完成。

未来 ASR 可通过 window.AIXiaotong.ingestTranscript(event) 输入片段：session_id、segment_id、revision、speaker（customer/operator）、text、as_of、is_final。未完成片段仅展示，完成片段触发建议，旧修订和过时结果会被丢弃。

## 项目结构

- frontend/index.html、styles.css：单页界面与样式。
- frontend/app.js：演示、转写接入缝、会话与归档。
- frontend/core.js：本地规则与结构化结果校验。
- frontend/server.py：本机静态服务与可选 Dify 网关。
- poc/customers.json：网关所需的 C001 合成快照。
- frontend/assets/：Logo 和虚构人物头像，来源说明见 ASSETS.md。

## 检查

```sh
node --test frontend/tests/core.test.cjs
python3 -m unittest discover -s frontend/tests -p 'test_*.py'
```

此仓库仅用于产品演示，不包含生产身份鉴权、跨设备存储、实际报价与办理服务。
