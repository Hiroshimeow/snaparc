import re, subprocess, sys
pats = [re.compile(r"AIza[A-Za-z0-9_-]{35}"), re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|ya29\\.[A-Za-z0-9._-]{20,}")]
def scan(diff):
    path = ""
    findings = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            if any(p.search(line[1:]) for p in pats):
                findings.append(path)
    return sorted(set(findings))
if __name__ == "__main__":
    output = subprocess.run(["git","diff","--cached","--no-ext-diff","--unified=0"],capture_output=True,text=True,encoding="utf-8",errors="replace")
    if output.returncode:
        sys.exit(2)
    findings = scan(output.stdout)
    if findings:
        print("BLOCKED secret-like values in staged files (values hidden):", *findings, sep="\\n", file=sys.stderr)
        sys.exit(1)
    print("Secret guard passed")
