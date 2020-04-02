output "Admin_UI" {
  value = "https://${opentelekomcloud_networking_floatingip_v2.floatip_1.address}:64294"
}

output "SSH_Access" {
  value = "ssh -p 64295 linux@${opentelekomcloud_networking_floatingip_v2.floatip_1.address}"
}

output "Web_UI" {
  value = "https://${opentelekomcloud_networking_floatingip_v2.floatip_1.address}:64297"
}
