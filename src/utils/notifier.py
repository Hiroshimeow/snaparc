import subprocess
import threading


def _run_toast(title, message):
    # Escape single quotes for PowerShell
    safe_title = title.replace("'", "''")
    safe_message = message.replace("'", "''")

    ps_script = f"""
    [reflection.assembly]::loadwithpartialname("System.Windows.Forms") | Out-Null
    [reflection.assembly]::loadwithpartialname("System.Drawing") | Out-Null
    $icon = New-Object System.Windows.Forms.NotifyIcon
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.Visible = $true
    $icon.ShowBalloonTip(3000, '{safe_title}', '{safe_message}', [System.Windows.Forms.ToolTipIcon]::Info)
    Start-Sleep -s 3
    $icon.Dispose()
    """

    try:
        subprocess.run(
            ["powershell", "-Command", ps_script],
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    except Exception as e:
        print(f"Error showing toast: {e}")


def show_toast(source, model_name):
    """
    Hiển thị thông báo Toast trên Windows.
    Format: Nguồn [source] | [model_name]
    """
    title = "SnapArc"
    message = f"Nguồn {source} | {model_name}"

    # Chạy trong thread riêng để không chặn luồng chính
    threading.Thread(target=_run_toast, args=(title, message), daemon=True).start()
