# ContentSwarm 托管认证模式

## 目标

HyCanvas 作为 ContentSwarm 内嵌设计工作台运行，不再向最终用户提供独立登录、注册或密码找回入口；所有新 HyCanvas 会话必须由 ContentSwarm 服务端签发的短期集成票据建立。

## 范围

- 使用 `HYCANVAS_AUTH_MODE=contentswarm` 显式开启，默认 `standalone` 行为不变。
- `CONTENTSWARM_URL` 指定可信的 ContentSwarm 地址；托管模式下必须是绝对 HTTP(S) URL。
- 登录、注册、密码找回、邮箱验证和邀请页统一重定向到 ContentSwarm 的 `/hycanvas` 工作台。
- 未登录访问 HyCanvas 首页、工作台或编辑器时，前端直接返回 ContentSwarm，不再呈现登录表单。
- 本地 HTTP 环境中，ContentSwarm 与 HyCanvas 会统一 `localhost` / loopback IP 主机名，避免内嵌会话 Cookie 因主机名混用而失效。
- 密码、Magic Link 和 OIDC 登录在服务端全部关闭；票据兑换、会话刷新和原有权限校验继续保留。

## 验收清单

- [x] 托管模式缺少有效 `CONTENTSWARM_URL` 时拒绝启动。
- [x] 独立认证页面返回 ContentSwarm HyCanvas 工作台。
- [x] 人工登录、注册接口被关闭。
- [x] ContentSwarm 菜单入口可通过工作区票据进入 HyCanvas。
- [x] 内容结果编辑入口沿用已验证的设计票据和站内返回链路。
- [x] 从视觉稿创建与继续编辑入口进入时不再绕过短期票据会话。
