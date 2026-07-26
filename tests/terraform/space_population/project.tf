resource "octopusdeploy_project_group" "test_group" {
  name        = "Test Group"
  description = "Test project group"
}

resource "octopusdeploy_project" "test_project" {
  auto_create_release                  = false
  default_guided_failure_mode          = "EnvironmentDefault"
  default_to_skip_if_already_installed = false
  description                          = "Test project with runbooks"
  discrete_channel_release             = false
  is_disabled                          = false
  is_discrete_channel_release          = false
  is_version_controlled                = false
  lifecycle_id                         = data.octopusdeploy_lifecycles.default.lifecycles[0].id
  name                                 = "Test Project"
  project_group_id                     = octopusdeploy_project_group.test_group.id
  tenanted_deployment_participation    = "Untenanted"
  space_id                             = var.octopus_space_id
  included_library_variable_sets       = []

  versioning_strategy {
    template = "#{Octopus.Version.LastMajor}.#{Octopus.Version.LastMinor}.#{Octopus.Version.LastPatch}.#{Octopus.Version.NextRevision}"
  }

  connectivity_policy {
    allow_deployments_to_no_targets = true
    exclude_unhealthy_targets       = false
    skip_machine_behavior           = "SkipUnavailableMachines"
  }
}
