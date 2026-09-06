# 携程购票助手 MVP（查询模式）

## 当前范围

第一版只做**单程机票查询和候选方案整理**：

```text
城市 + 出发日期
→ 填写查询条件
→ 读取可见航班结果
→ 汇总最多 5 个候选航班
→ 结束
```

它不会登录、不会输入乘机人、不会进入订单确认页、不会下单、不会支付、不会处理验证码。

## 代码级安全限制

`BrowserUseTool` 的 `ctrip_query_mode` 在专用运行器中强制开启。它会拒绝：

- 任何非 `ctrip.com` / `trip.com` 域名；
- 登录、验证、订单、提交或支付页面；
- `gui_action`、文件上传、图片粘贴、外部网页搜索、新建标签页；
- 登录、验证码、乘机人、提交订单和支付相关的输入文本；
- 未列入查询白名单的浏览器动作。

遇到网站登录/验证码/风控时，助手必须停止并请求人工接管；不尝试绕过。

## 运行

在已设置 `DASHSCOPE_API_KEY` 的同一个 PowerShell 中：

```powershell
cd D:\It\Test_Project\Openmanus-Project\upstream\manus-gui

.\.venv\Scripts\python.exe .\ctrip_query_assistant.py `
  --origin 上海 `
  --destination 北京 `
  --date 2026-09-25 `
  --max-steps 12
```

结果是实时网页观察，航班和价格随页面、日期、库存变化；助手不会生成订单或支付。
