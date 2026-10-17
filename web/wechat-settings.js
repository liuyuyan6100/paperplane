"use strict";

function wechatSettings(settings) {
  const channel = settings.channels.find((item) => item.id === "wechat");
  return `<section class="panel"><div class="panel-head"><h2>微信公众号</h2></div><div class="panel-body">
  <p class="tip">由工作台上传封面和正文图片、保存微信草稿，并读回核对。处理结果保存在发布队列。</p>
  <label>公众号交付方式<select id="wechat-mode"><option value="api" ${channel?.config.mode === "api" ? "selected" : ""}>自动送入公众号草稿箱</option><option value="manual" ${channel?.config.mode !== "api" ? "selected" : ""}>导出发布包，手动上传</option></select></label>
  <label>AppID<input id="wechat-app-id" value="${esc(settings.wechat_app_id)}" autocomplete="off"></label>
  <label>AppSecret<input id="wechat-secret" type="password" autocomplete="new-password" placeholder="${settings.wechat_secret_set ? "已配置，留空保留" : "从公众号开发配置填写"}"></label>
  <details class="spaced"><summary>连接设置：固定出口或本机代理</summary>
  <p class="tip">微信需要 IP 白名单。能直连时保持为空；已有固定出口时，工作台可以自行建立连接。</p>
  <label>已有本机代理<input id="wechat-proxy" value="${esc(settings.wechat_proxy)}" placeholder="http://127.0.0.1:端口 或 socks5h://127.0.0.1:端口"></label>
  <p class="tip">使用下方固定出口时，请将上方代理留空。</p>
  <label>固定出口 SSH 主机<input id="wechat-ssh-host" value="${esc(settings.wechat_ssh_host)}" placeholder="用户名@主机地址"></label>
  <label>本机 SSH 私钥路径<input id="wechat-ssh-key" value="${esc(settings.wechat_ssh_key)}" placeholder="已有私钥文件的绝对路径"></label>
  <label>主机校验名称（可选）<input id="wechat-ssh-alias" value="${esc(settings.wechat_ssh_host_alias)}" placeholder="known_hosts 中已有的主机名称"></label>
  <label>本机连接端口<input id="wechat-ssh-port" type="number" min="1024" max="65535" value="${esc(settings.wechat_ssh_port || "1281")}"></label>
  <p class="tip">需要本机 OpenSSH 和已验证的主机记录。私钥只在本机使用；关闭工作台服务会结束它创建的连接。</p></details>
  <button class="button spaced" data-action="test-wechat">保存并测试公众号连接</button><p class="tip">测试只读取草稿数量和权限。实际送稿需在文章中运行发布检查并创建任务。</p><div id="wechat-test-result" role="status"></div>
  </div></section>`;
}

function wechatSettingsValues() {
  return {WECHAT_APP_ID: $("#wechat-app-id").value, WECHAT_APP_SECRET: $("#wechat-secret").value,
    WECHAT_PROXY: $("#wechat-proxy").value, WECHAT_SSH_HOST: $("#wechat-ssh-host").value,
    WECHAT_SSH_KEY: $("#wechat-ssh-key").value, WECHAT_SSH_HOST_ALIAS: $("#wechat-ssh-alias").value,
    WECHAT_SSH_PORT: $("#wechat-ssh-port").value};
}
