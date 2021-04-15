output "Admin_UI" {
  value = "https://${opentelekomcloud_vpc_eip_v1.eip_1.publicip.0.ip_address}:64294"
}

output "SSH_Access" {
  value = "ssh -p 64295 linux@${opentelekomcloud_vpc_eip_v1.eip_1.publicip.0.ip_address}"
}

output "Web_UI" {
  value = "https://${opentelekomcloud_vpc_eip_v1.eip_1.publicip.0.ip_address}:64297"
}
