import time
import urllib.request
import urllib.error
import smtplib
import datetime
import os
import socket
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

# 引入腾讯云 SDK
try:
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.cbs.v20170312 import cbs_client, models
except ImportError:
    print("❌ 错误: 请先安装 SDK: pip3 install tencentcloud-sdk-python")
    exit()

# ================= 配置区域 =================
# 1. 邮件配置
MAIL_SENDER = 'xlj@xlj0.com' # 发件人
MAIL_PASSWORD = '这里是你的邮件密码'
MAIL_RECEIVER = '480003862@qq.com' # 收件人
SMTP_SERVER = 'smtp.qq.com'
SMTP_PORT = 465

# 2. 腾讯云 API 配置
TENCENT_SECRET_ID = '这是你的 腾讯云API秘钥ID'
TENCENT_SECRET_KEY = '这是你的 腾讯云API秘钥ID KEY (去腾讯云获取)'
REGION = 'ap-chongqing' # 重庆 (这里是服务器区域)

# 3. 备份保留策略
# 设置为 7 表示快照会在 7 天后自动删除
# 建议设置 3-7 天，足够你恢复数据，又不会长期扣费
RETENTION_DAYS = 7 
# ===========================================

BASE_URL = "http://metadata.tencentyun.com/latest/meta-data/"
SPOT_URL = BASE_URL + "spot/termination-time"

def log(msg):
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def get_meta_data(key):
    try:
        url = BASE_URL + key
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.read().decode('utf-8')
    except:
        return "获取失败"

def get_system_specs():
    specs = {}
    try:
        specs['cpu_count'] = os.cpu_count()
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if 'MemTotal' in line:
                    kb = int(line.split()[1])
                    specs['ram_total'] = f"{kb / 1024 / 1024:.2f} GB"
                    break
    except:
        specs['cpu_count'] = "未知"
        specs['ram_total'] = "未知"
    return specs

def trigger_auto_snapshot(instance_id):
    """调用腾讯云 API 创建带过期时间的快照"""
    log("⚡️ 正在尝试自动创建快照/备份...")
    os.system('sync') 
    log("✅ 系统缓存已写入磁盘 (sync executed).")

    try:
        cred = credential.Credential(TENCENT_SECRET_ID, TENCENT_SECRET_KEY)
        client = cbs_client.CbsClient(cred, REGION)

        # 1. 查找硬盘
        req_desc = models.DescribeDisksRequest()
        req_desc.Filters = [{"Name": "instance-id", "Values": [instance_id]}]
        resp_desc = client.DescribeDisks(req_desc)
        
        disk_list = resp_desc.DiskSet
        snapshot_results = []

        if not disk_list:
            log("❌ 未找到挂载的云硬盘，无法备份！")
            return "备份失败：无硬盘"

        # 2. 计算过期时间 (UTC格式)
        # 腾讯云要求格式: 2022-01-08T09:47:55+00:00
        utc_now = datetime.datetime.utcnow()
        deadline_dt = utc_now + datetime.timedelta(days=RETENTION_DAYS)
        deadline_str = deadline_dt.strftime('%Y-%m-%dT%H:%M:%S+00:00')
        log(f"🕒 快照将设置为 {RETENTION_DAYS} 天后自动过期 ({deadline_str})")

        # 3. 循环创建快照
        for disk in disk_list:
            disk_id = disk.DiskId
            disk_usage = disk.DiskUsage
            
            req_snap = models.CreateSnapshotRequest()
            req_snap.DiskId = disk_id
            req_snap.SnapshotName = f"AutoBackup_{disk_usage}_{datetime.datetime.now().strftime('%Y%m%d%H%M')}"
            
            # 🔥 关键修改：添加 Deadline 参数
            req_snap.Deadline = deadline_str
            
            resp_snap = client.CreateSnapshot(req_snap)
            snap_id = resp_snap.SnapshotId
            
            log(f"✅ 快照创建成功: {disk_id} -> {snap_id}")
            snapshot_results.append(f"<span style='color:green'>成功 (ID: {snap_id})</span><br><small>有效期至: {deadline_str}</small>")

        return "<br>".join(snapshot_results)

    except Exception as err:
        log(f"❌ API 报错: {err}")
        return f"<span style='color:red'>备份失败: {str(err)}</span>"

def send_email(is_test=False, termination_time="无", backup_status="未触发"):
    try:
        instance_id = get_meta_data("instance-id")
        public_ip = get_meta_data("public-ipv4")
        local_ip = get_meta_data("local-ipv4")
        zone = get_meta_data("placement/zone")
        specs = get_system_specs()
        
        if is_test:
            subject = "【✅监控启动】服务器安全守护中"
            title_color = "#28a745"
            warn_text = "监控脚本已成功启动，正在后台运行。"
        else:
            subject = "【🚨紧急】竞价实例销毁预警 & 自动备份"
            title_color = "#dc3545"
            warn_text = f"<b>警告：腾讯云已发出回收通知！</b><br>预计销毁时间：{termination_time}"

        html_content = f"""
        <html>
        <body>
            <div style="font-family: '微软雅黑', sans-serif; max-width: 600px; border: 1px solid #ddd; border-radius: 8px; overflow: hidden;">
                <div style="background-color: {title_color}; color: white; padding: 15px; text-align: center;">
                    <h2 style="margin:0;">{subject}</h2>
                </div>
                <div style="padding: 20px;">
                    <p style="font-size: 16px;">{warn_text}</p>
                    
                    <h3 style="border-bottom: 2px solid #eee; padding-bottom: 10px;">📊 服务器详细信息</h3>
                    <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                        <tr style="background-color: #f9f9f9;"><td style="padding: 8px; width: 30%;"><b>实例 ID</b></td><td style="padding: 8px;">{instance_id}</td></tr>
                        <tr><td style="padding: 8px;"><b>公网 IP</b></td><td style="padding: 8px;">{public_ip}</td></tr>
                        <tr style="background-color: #f9f9f9;"><td style="padding: 8px;"><b>内网 IP</b></td><td style="padding: 8px;">{local_ip}</td></tr>
                        <tr><td style="padding: 8px;"><b>所在区域</b></td><td style="padding: 8px;">{zone}</td></tr>
                        <tr style="background-color: #f9f9f9;"><td style="padding: 8px;"><b>硬件配置</b></td><td style="padding: 8px;">CPU: {specs['cpu_count']} 核 / 内存: {specs['ram_total']}</td></tr>
                    </table>

                    <h3 style="border-bottom: 2px solid #eee; padding-bottom: 10px; margin-top: 20px;">💾 自动备份状态</h3>
                    <div style="background-color: #f0f0f0; padding: 10px; border-radius: 4px;">
                        {backup_status}
                    </div>
                    
                    <p style="margin-top: 30px; text-align: right; font-size: 14px;">
                        From: <a href="https://xlj0.com" style="color: #007bff; text-decoration: none; font-weight: bold;">喜樂君</a>
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        message = MIMEText(html_content, 'html', 'utf-8')
        message['From'] = formataddr(["服务器监控", MAIL_SENDER])
        message['To'] = formataddr(["管理员", MAIL_RECEIVER])
        message['Subject'] = Header(subject, 'utf-8')

        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(MAIL_SENDER, MAIL_PASSWORD)
        server.sendmail(MAIL_SENDER, [MAIL_RECEIVER], message.as_string())
        server.quit()
        log("📧 邮件发送成功！")
        return True
    except Exception as e:
        log(f"📧 邮件发送失败: {e}")
        return False

def check_spot_status():
    log("🛡️ 监控脚本已启动，开始守护...")
    
    # 启动时发送一封测试邮件
    send_email(is_test=True, backup_status=f"系统正常。策略：快照将在创建 {RETENTION_DAYS} 天后自动删除。")
    
    while True:
        try:
            req = urllib.request.Request(SPOT_URL)
            with urllib.request.urlopen(req, timeout=2) as response:
                data = response.read().decode('utf-8')
                
                if "20" in data:
                    log(f"!!! 🚨 收到销毁通知 !!! 时间: {data}")
                    
                    instance_id = get_meta_data("instance-id")
                    if "ins-" in instance_id:
                        backup_res = trigger_auto_snapshot(instance_id)
                    else:
                        backup_res = "❌ 失败：无法获取实例ID"
                    
                    send_email(is_test=False, termination_time=data, backup_status=backup_res)
                    log("任务完成，脚本退出。")
                    break 
                    
        except urllib.error.HTTPError as e:
            if e.code == 404:
                pass 
        except Exception as e:
            pass
            
        time.sleep(5) 

if __name__ == "__main__":
    check_spot_status()
