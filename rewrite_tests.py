import re
from pathlib import Path

file_path = Path("tests/test_remediation_engine.py")
content = file_path.read_text()

# Add sys.path and imports
new_imports = """import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1] / "src" / "lambda"))
from remediation_engine import remediation_ec2, remediation_s3, remediation_iam, remediation_ebs
"""

content = re.sub(r'import botocore.exceptions\nimport pytest\n', new_imports + '\nimport botocore.exceptions\nimport pytest\n', content)

# Remove fixtures
content = re.sub(r'@pytest\.fixture\ndef remediation_\w+_module\(\):\n(?:    .*\n)*?    return module\n\n\n', '', content)

# Replace module arguments in tests
content = re.sub(r'def (test_\w+)\(remediation_(\w+)_module(?:, caplog)?\):', r'def \1(caplog=None):\n    if caplog is None:\n        pass' , content) 
# wait, actually we can just remove `remediation_xxx_module` argument and leave caplog if it exists.
