#!/usr/bin/env bash
# 系统信息采集脚本：硬件配置 / 网络环境 / 运行环境
# 用法: ./sysinfo_dump.sh [输出文件]
#     不传参数则输出到 ./sysinfo_<hostname>_<时间戳>.txt

set -uo pipefail

OUT="${1:-./sysinfo_$(hostname -s 2>/dev/null || echo host)_$(date +%Y%m%d_%H%M%S).txt}"
mkdir -p "$(dirname "$OUT")" 2>/dev/null

section() {
    printf '\n\n'
    printf '=%.0s' {1..78}; echo
    echo "## $*"
    printf '=%.0s' {1..78}; echo
}

sub() {
    echo
    echo "--- $* ---"
}

# 有命令才执行，否则说明缺失
have() { command -v "$1" >/dev/null 2>&1; }

run() {
    local cmd="$1"
    local bin="${cmd%% *}"
    if have "$bin"; then
        bash -c "$cmd" 2>&1
    else
        echo "[跳过] 未安装: $bin"
    fi
}

exec > >(tee "$OUT") 2>&1

echo "系统信息快照"
echo "采集时间: $(date '+%Y-%m-%d %H:%M:%S %Z (%z)')"
echo "采集主机: $(hostname 2>/dev/null)"


section "1. 基本系统"
sub "发行版"
run "cat /etc/os-release"
sub "内核 / 架构"
run "uname -a"
sub "主机名 / 硬件平台"
echo "hostname: $(hostname 2>/dev/null)"
echo "FQDN    : $(hostname -f 2>/dev/null)"
echo "arch    : $(uname -m)"
[ -r /proc/device-tree/model ] && echo "设备型号: $(tr -d '\0' </proc/device-tree/model)"
sub "运行时长 / 负载"
run "uptime"
run "cat /proc/loadavg"
sub "启动参数"
run "cat /proc/cmdline"
sub "虚拟化 / 容器检测"
if have systemd-detect-virt; then
    echo "虚拟化: $(systemd-detect-virt 2>/dev/null)"
fi
echo "cgroup: $(stat -fc %T /sys/fs/cgroup 2>/dev/null)"
if [ -n "${KUBERNETES_SERVICE_HOST:-}" ] || [ -d /var/run/secrets/kubernetes.io ]; then
    echo "容器: Kubernetes Pod (node=$(hostname), ns=$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace 2>/dev/null || echo '?'))"
elif [ -f /.dockerenv ]; then
    echo "容器: Docker"
elif [ -f /run/.containerenv ]; then
    echo "容器: Podman/Container"
else
    echo "容器: 未检测到明确的容器标记文件"
fi
echo "PID1: $(cat /proc/1/comm 2>/dev/null)"


section "2. CPU"
sub "lscpu"
run "lscpu"
sub "物理核心 / 逻辑核心 / 型号"
echo "逻辑核心数: $(nproc 2>/dev/null || echo '?')"
echo "型号      : $(awk -F: '/model name/{print $2; exit}' /proc/cpuinfo | sed 's/^ *//')"
echo "插槽数    : $(awk -F: '/physical id/{print $2}' /proc/cpuinfo | sort -u | wc -l)"
echo "物理核心  : $(awk -F: '/cpu cores/{print $2; exit}' /proc/cpuinfo | sed 's/^ *//')"
sub "CPU 频率"
run "grep -m4 'cpu MHz' /proc/cpuinfo"
sub "缓存"
run "lscpu | grep -i cache"


section "3. 内存 / Swap"
sub "free -h"
run "free -h"
sub "/proc/meminfo 摘要"
run "grep -E 'MemTotal|MemFree|MemAvailable|Buffers|^Cached|SwapTotal|SwapFree|Dirty' /proc/meminfo"
sub "cgroup 内存限额（容器内有效）"
for f in /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory/memory.limit_in_bytes; do
    [ -r "$f" ] && echo "$f = $(cat "$f")"
done


section "4. 磁盘 / 文件系统"
sub "块设备"
run "lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL"
sub "磁盘占用"
run "df -hT -x tmpfs -x devtmpfs"
sub "inode 占用"
run "df -i -x tmpfs -x devtmpfs"
sub "挂载点"
run "findmnt -rn -o TARGET,SOURCE,FSTYPE,OPTIONS | head -40"


section "5. 网络环境"
sub "主机名解析"
run "cat /etc/hosts"
sub "DNS 配置"
run "cat /etc/resolv.conf"
sub "网卡与地址"
run "ip -br addr"
run "ip -4 addr show"
sub "路由表"
run "ip route"
sub "邻居 / ARP"
run "ip neigh"
sub "监听端口"
run "ss -tulnp"
sub "活跃连接"
run "ss -tnp state established | head -30"
sub "防火墙"
if have ufw; then ufw status 2>&1; else echo "[跳过] 未安装 ufw"; fi
if have iptables; then echo "[iptables]"; iptables -S 2>&1 | head -30; else echo "[跳过] 未安装 iptables"; fi
if have nft; then echo "[nft]"; nft list ruleset 2>&1 | head -30; fi
sub "网络接口统计"
run "cat /proc/net/dev"
sub "代理环境变量"
env | grep -iE '^(http_proxy|https_proxy|all_proxy|no_proxy|ftp_proxy|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY)=' || echo "（未设置代理环境变量）"

sub "代理链路 / 出口探测"
if have curl; then
    PROXY_URL="${https_proxy:-${HTTPS_PROXY:-}}"
    if [ -n "$PROXY_URL" ]; then
        PHOST=$(printf '%s' "$PROXY_URL" | sed -E 's#^[a-zA-Z]+://##; s#/.*$##')
        PPORT="${PHOST##*:}"
        echo "代理地址   : $PROXY_URL"
        echo "监听状态   : $(ss -tlnH "sport = :$PPORT" 2>/dev/null | awk '{print $4}' | paste -sd' ' -)"
        echo -n "无凭据探测 : "
        curl -sS -D /tmp/.ph -o /dev/null --max-time 5 "http://$PHOST/" 2>/dev/null || true
        head -1 /tmp/.ph 2>/dev/null | tr -d '\r' || echo "无响应"
        grep -i 'proxy-authenticate' /tmp/.ph 2>/dev/null | tr -d '\r'
        echo -n "相邻端口   : "
        curl -sS -D /tmp/.ph2 -o /dev/null --max-time 5 "http://127.0.0.1:$((PPORT + 1))/" 2>/dev/null || true
        head -1 /tmp/.ph2 2>/dev/null | tr -d '\r' | tr '\n' ' '
        grep -i 'proxy-authenticate' /tmp/.ph2 2>/dev/null | tr -d '\r'
        echo
        echo "代理认证环境变量: $(env | grep -ciE 'proxy_(user|pass|auth)|PROXY_TOKEN' || true) 个命中"
        echo
        echo "经代理访问（HTTP 状态码，000=失败/被阻断）:"
        for u in https://github.com https://pypi.org https://registry.npmjs.org \
                 https://mirrors.aliyun.com https://example.com https://www.google.com; do
            c=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$u" 2>/dev/null || true)
            printf '  %-34s %s\n' "$u" "${c:-000}"
        done
        echo
        echo -n "绕过代理直连 example.com:443 -> "
        d=$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 6 https://example.com 2>/dev/null || true)
        [ "${d:-000}" = "000" ] && echo "失败（说明出口必须走代理）" || echo "$d（可直连）"
        echo -n "经代理出口 IP -> "
        eip=$(curl -sS -x "$PROXY_URL" --max-time 8 https://ifconfig.me/ip 2>/dev/null | tail -1)
        echo "${eip:-获取失败}"
        echo -n "CONNECT 隧道 -> "
        if have nc; then
            r=$(printf 'CONNECT github.com:443 HTTP/1.1\r\nHost: github.com:443\r\n\r\n' | timeout 6 nc 127.0.0.1 "$PPORT" 2>/dev/null | head -1 | tr -d '\r')
            echo "${r:-无响应}"
        else
            echo "[跳过] 无 nc"
        fi
    else
        echo "未检测到代理环境变量，直接测试公网连通性:"
        echo -n "  https://github.com -> "
        curl -sS -o /dev/null -w '%{http_code}\n' --max-time 8 https://github.com 2>/dev/null || echo "失败"
    fi
    echo
    echo "TCP 连通性（裸 TCP，不走代理）:"
    for hp in 1.1.1.1:443 8.8.8.8:53 140.82.121.4:443 "$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf):53"; do
        h=${hp%:*}; p=${hp#*:}
        if timeout 4 bash -c "cat </dev/null >/dev/tcp/$h/$p" 2>/dev/null; then
            printf '  %-20s OK\n' "$hp"
        else
            printf '  %-20s FAIL\n' "$hp"
        fi
    done
else
    echo "[跳过] 未安装 curl"
fi

sub "关键域名解析"
if have getent; then
    for d in github.com pypi.org registry.npmjs.org mirrors.aliyun.com www.google.com api.openai.com; do
        printf '%-26s %s\n' "$d" "$(getent ahostsv4 "$d" 2>/dev/null | awk '{print $1}' | sort -u | paste -sd',' -)"
    done
fi
sub "socket 统计"
run "ss -s"


section "6. 进程 / 服务"
sub "进程总数 / 线程数"
echo "进程数: $(ls /proc | grep -cE '^[0-9]+$')"
run "ps -eo pid,ppid,%cpu,%mem,rss,etime,comm --sort=-%cpu | head -16"
sub "内存占用 TOP10"
run "ps -eo pid,%mem,rss,comm --sort=-rss | head -11"
sub "systemd 服务（若可用）"
if have systemctl && [ -d /run/systemd/system ]; then
    systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null | head -30
else
    echo "[跳过] 无 systemd 或非 init 环境"
fi


section "7. 硬件 / 外设"
sub "PCI 设备"
run "lspci"
sub "USB 设备"
run "lsusb"
sub "DMI / 主板信息"
run "dmidecode -t system -t baseboard"
sub "温度 / 风扇"
run "sensors"
sub "GPU"
if have nvidia-smi; then nvidia-smi; else echo "[跳过] 无 nvidia-smi"; fi


section "8. 软件运行环境"
sub "常用运行时版本"
for c in python3 python node npm go java rustc gcc make git docker podman kubectl uv; do
    if have "$c"; then
        printf '%-8s ' "$c"
        case "$c" in
            python*) "$c" --version 2>&1 | head -1 ;;
            go) "$c" version 2>&1 | head -1 ;;
            node|npm|java|rustc|gcc|make|git|docker|podman|kubectl|uv) "$c" --version 2>&1 | head -1 ;;
            *) "$c" --version 2>&1 | head -1 ;;
        esac
    fi
done
sub "环境变量（敏感值脱敏，超长值截断）"
env | sort | awk '
{
    i = index($0, "=")
    k = substr($0, 1, i - 1); v = substr($0, i + 1)
    if (tolower(k) ~ /(key|token|secret|password|passwd|credential|auth)/) v = "***REDACTED***"
    else if (length(v) > 300) v = substr(v, 1, 300) "...<truncated>"
    printf "%s=%s\n", k, v
}'


section "9. 系统限制"
sub "ulimit"
run "ulimit -a"
sub "内核参数（关键项）"
run "sysctl -a 2>/dev/null | grep -E '^(net.core.somaxconn|net.ipv4.ip_forward|net.ipv4.tcp_max_syn_backlog|vm.swappiness|vm.overcommit_memory|kernel.pid_max|fs.file-max|fs.inotify.max_user_watches)'"
sub "已加载模块数"
if [ -r /proc/modules ]; then
    echo "模块数: $(wc -l </proc/modules)"
else
    echo "模块: /proc/modules 不可用（容器/精简内核环境）"
fi

echo
echo "采集完成 -> $OUT"
