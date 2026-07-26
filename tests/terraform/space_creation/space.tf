resource "octopusdeploy_space" "test_space" {
  name                        = "Test Space"
  is_default                  = false
  is_task_queue_stopped       = false
  space_managers_team_members = null
  space_managers_teams        = ["teams-administrators"]
}

output "octopus_space_id" {
  value = octopusdeploy_space.test_space.id
}
