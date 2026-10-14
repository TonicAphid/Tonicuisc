# 放行 Tonicuisc 需要的入站端口（家里部署：外部走 IPv6，所以这条规则必须生效）。
#
# 用法（**管理员** PowerShell）：
#   pwsh -File deploy/open-firewall.ps1            # 放行 TCP 80 / 443
#   pwsh -File deploy/open-firewall.ps1 -Remove    # 撤销这两条规则
#
# 只动这两条具名规则，不动系统里别的防火墙设置。
# 注意：Windows 防火墙只是第一层，光猫 / 路由器上的 IPv6 防火墙也要放行，
# 否则外面照样进不来（见 deploy/Caddyfile 顶部说明）。

param([switch]$Remove)

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error '需要管理员权限：请用「以管理员身份运行」的 PowerShell 执行本脚本。'
    exit 1
}

# 80 = Let's Encrypt HTTP-01 校验；443 = 正式 HTTPS 入口
$rules = @(
    @{ Name = 'Tonicuisc HTTPS (443)'; Port = 443 },
    @{ Name = 'Tonicuisc ACME (80)'; Port = 80 }
)

foreach ($rule in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
    if ($Remove) {
        if ($existing) {
            Remove-NetFirewallRule -DisplayName $rule.Name
            Write-Host "已撤销：$($rule.Name)"
        } else {
            Write-Host "本来就没有：$($rule.Name)"
        }
        continue
    }
    if ($existing) {
        Write-Host "已存在，跳过：$($rule.Name)"
        continue
    }
    # 不指定 -LocalAddress：IPv4 / IPv6 的入站都放行（家里走的是 IPv6）
    New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $rule.Port -Profile Any | Out-Null
    Write-Host "已放行 TCP $($rule.Port)：$($rule.Name)"
}

if (-not $Remove) {
    Write-Host ''
    Write-Host '别忘了路由器 / 光猫那一层：IPv6 防火墙要放行 TCP 80/443 到这台机器。'
    Write-Host '验证：从手机（走流量，别连家里 Wi-Fi）打开 https://uisc.tonicaphid.cn/api/health'
}
