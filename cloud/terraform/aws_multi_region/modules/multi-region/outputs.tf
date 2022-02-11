output "Admin_UI" {
  value = "https://${aws_instance.tpot.public_dns}:64294/"
}

output "SSH_Access" {
  value = "ssh -i {private_key_file} -p 64295 admin@${aws_instance.tpot.public_dns}"
}

output "Web_UI" {
  value = "https://${aws_instance.tpot.public_dns}:64297/"
}

