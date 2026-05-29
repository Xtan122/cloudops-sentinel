# 🛡️ CloudOps Sentinel

**Nền tảng Quản trị Đám mây Tự động & Hạ tầng Tự phục hồi (Self-Healing)**

CloudOps Sentinel là một nền tảng quản trị đám mây tự động (Cloud Governance) trên AWS giúp thực thi các tiêu chuẩn bảo mật và tối ưu chi phí theo thời gian thực. Hệ thống không chỉ phát hiện vi phạm mà còn tự động sửa lỗi (Auto-remediation) và sử dụng AI (Amazon Bedrock) để cung cấp báo cáo ngữ cảnh thông minh cho đội ngũ vận hành.

## 🏗️ Kiến trúc Hệ thống

Dự án này được xây dựng như một lớp "phòng thủ chủ động" bên trên hệ thống hạ tầng AWS:

* **Ingestion:** AWS EventBridge bắt các sự kiện thay đổi trạng thái (EC2 Start, S3 Policy Change, IAM Key Create).
* **Brain (Lambda):** Chứa logic kiểm tra tuân thủ (Compliance Check) và thực thi (Remediation).
* **AI Layer:** Amazon Bedrock (Claude 3 Haiku) phân tích sự kiện (payload) để viết thông báo Slack thông minh và có ngữ cảnh.
* **Safety Valve (Cơ chế an toàn):** Tính năng **Dry-run** và **Exclusion Tagging** giúp tránh can thiệp nhầm vào các tài nguyên sản xuất quan trọng.

## 🛠️ Các Guardrails Cốt Lõi

| Loại hình | Kịch bản vi phạm (Violation) | Hành động tự động (Remediation) |
| :--- | :--- | :--- |
| **Chi phí (Cost)** | EC2 Instance tạo mới mà không có tag `Owner` hoặc `Project`. | Gửi cảnh báo Slack -> Sau 1 giờ nếu không bổ sung tag sẽ tự động **Stop**. |
| **Bảo mật (Security)** | S3 Bucket bị thay đổi chính sách sang **Public Access**. | Ngay lập tức gỡ bỏ Public Access Policy, đưa Bucket về trạng thái **Private**. |
| **IAM** | Người dùng tạo **IAM Access Key** thay vì dùng IAM Roles cho ứng dụng. | Tự động **Deactivate** Key và gửi thông báo hướng dẫn sử dụng Role. |
| **Tuân thủ (Compliance)** | EBS Volume không được mã hóa (Unencrypted). | Gửi thông báo mức độ High và gắn Tag cảnh báo `Non-Compliant`. |

## 🛡️ Cơ Chế An Toàn & Kiểm Soát

* **Chế độ Dry-run:** Cho phép hệ thống chỉ gửi cảnh báo mà không thực hiện hành động khắc phục thực tế (dùng để kiểm thử trước khi áp dụng thật).
* **Exclusion Tags (Tag miễn trừ):** Nếu tài nguyên có tag `Skip-Enforcement: true`, hệ thống sẽ bỏ qua mọi quy tắc kiểm tra và không can thiệp vào tài nguyên đó.
* **Human-in-the-loop (Phê duyệt thủ công):** Đối với các hành động nguy hiểm có rủi ro cao, hệ thống sẽ gửi một tin nhắn Slack kèm nút bấm **[Approve] / [Reject]** để yêu cầu người dùng xác nhận trước khi thực thi.

## 🤖 Báo Cáo Thông Minh Bằng AI (Amazon Bedrock)

Thay vì gửi log thô dưới dạng JSON, hệ thống sử dụng AI để dịch các sự kiện thành ngôn ngữ tự nhiên:
> ⚠️ **Phát hiện vi phạm chính sách!**
> * **Người thực hiện:** `Thanh.Nguyen` (Intern)
> * **Hành động:** Vừa tạo 1 con `p3.2xlarge` (GPU) tại Region `us-east-1`.
> * **Vấn đề:** Tài nguyên này không nằm trong danh mục cho phép và thiếu tag chi phí.
> * **Xử lý:** Sentinel đã tự động **Stop** máy sau 5 phút để tránh lãng phí tiền (tiết kiệm ước tính ~$73/ngày).

## 🚀 CI/CD Pipeline Chuẩn Enterprise

* **Infrastructure as Code (IaC):** Triển khai toàn bộ hạ tầng bằng Terraform Modules với Remote State.
* **Quét bảo mật (Security Scan):** Sử dụng `Checkov` hoặc `tfsec` để quét mã nguồn hạ tầng trước khi triển khai, đảm bảo không có lỗ hổng (Zero critical issues).
* **Kiểm thử tự động (Unit Testing):** Sử dụng `Pytest` và `moto` giả lập (mocking) AWS API để kiểm thử logic của các Lambda functions.
* **Tự động triển khai:** GitHub Actions sử dụng **OIDC** để xác thực an toàn và triển khai tự động lên AWS.

## ⚙️ Quản Lý Cấu Hình

Cấu hình cho các quy tắc, mức độ nghiêm trọng, thời gian chờ (timeout) và các yêu cầu về tag có thể được tinh chỉnh thông qua file cấu hình JSON. Sự thay đổi cấu hình được cập nhật tự động mà không cần phải triển khai lại Lambda.

## 📖 Tài Liệu Liên Quan

* [English README (README.md)](./README.md)
