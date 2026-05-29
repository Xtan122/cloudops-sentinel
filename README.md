# 🛡️ CloudOps Sentinel

**Automated Governance & Self-Healing Infrastructure Platform on AWS**

CloudOps Sentinel is an automated cloud governance platform designed to enforce security standards and optimize costs in real-time. The system not only detects policy violations but also performs auto-remediation and leverages AI (Amazon Bedrock) to generate intelligent, context-aware operational reports.

## 🏗️ Architecture

This project is built as an "active defense" layer on top of AWS infrastructure:

* **Ingestion:** AWS EventBridge captures state change events (e.g., EC2 Start, S3 Policy Change, IAM Key Create).
* **Brain (Lambda):** Contains the core logic for Compliance Checks and Execution (Remediation).
* **AI Layer:** Amazon Bedrock (Claude 3 Haiku) analyzes payloads to generate human-readable, highly contextual Slack notifications.
* **Safety Valve:** **Dry-run mode** and **Exclusion Tagging** prevent accidental intervention on critical resources.

## 🛠️ Core Guardrails

| Category | Violation Scenario | Auto-Remediation |
| :--- | :--- | :--- |
| **Cost** | EC2 Instance launched without `Owner` or `Project` tags. | Sends a Slack alert -> Automatically **Stops** the instance if tags are not added within 1 hour. |
| **Security** | S3 Bucket policy is changed to allow **Public Access**. | Immediately revokes public access policies, reverting the bucket to **Private**. |
| **IAM** | User creates an **IAM Access Key** instead of using IAM Roles. | Automatically **Deactivates** the key and sends a guide on using IAM Roles. |
| **Compliance** | EBS Volume is created unencrypted. | Sends a High-severity alert and adds a `Non-Compliant` warning tag. |

## 🛡️ Safety Mechanisms (Human-in-the-loop & Controls)

* **Dry-run Mode:** Allows the system to evaluate rules and send alerts without actually executing the remediation actions (useful for testing before enforcement).
* **Exclusion Tags:** If a resource is tagged with `Skip-Enforcement: true`, the system will bypass all compliance checks and remediation for it.
* **Human-in-the-loop:** For high-risk actions (e.g., Terminate/Delete), the system sends an interactive Slack message with **[Approve] / [Reject]** buttons.

## 🤖 AI-Driven Reporting (Amazon Bedrock)

Instead of sending raw JSON logs, Amazon Bedrock transforms alerts into natural language:
> ⚠️ **Policy Violation Detected!**
> * **User:** `Thanh.Nguyen` (Intern)
> * **Action:** Launched a `p3.2xlarge` (GPU) instance in `us-east-1`.
> * **Issue:** This resource is not in the allowed list and is missing cost-allocation tags.
> * **Resolution:** Sentinel automatically **Stopped** the instance after 5 minutes to prevent waste (estimated savings: ~$73/day).

## 🚀 Enterprise CI/CD Pipeline

* **Infrastructure as Code (IaC):** Deployed using Terraform modules with remote state management.
* **Security Scanning:** Infrastructure code is scanned using `Checkov` or `tfsec` before deployment.
* **Unit Testing:** Python Lambda functions are tested using `pytest` and `moto` (mocking AWS APIs).
* **Automated Deployment:** GitHub Actions uses **OIDC** for secure authentication and deployment to AWS.

## ⚙️ Configuration

Guardrail rules, severity levels, timeouts, and required tags can be configured easily via a JSON configuration file. Changes to this file take effect without requiring Lambda redeployments.

## 📖 Related Documents

* [Vietnamese README (README.vi.md)](./README.vi.md)