resource "octopusdeploy_project" "cac_test_project" {
  auto_create_release                  = false
  default_guided_failure_mode          = "EnvironmentDefault"
  default_to_skip_if_already_installed = false
  description                          = "CaC test project with runbooks stored in git"
  discrete_channel_release             = false
  is_disabled                          = false
  is_discrete_channel_release          = false
  is_version_controlled                = true
  lifecycle_id                         = data.octopusdeploy_lifecycles.default.lifecycles[0].id
  name                                 = "CaC Test Project"
  project_group_id                     = octopusdeploy_project_group.test_group.id
  tenanted_deployment_participation    = "Untenanted"
  space_id                             = var.octopus_space_id
  included_library_variable_sets       = []

  connectivity_policy {
    allow_deployments_to_no_targets = true
    exclude_unhealthy_targets       = false
    skip_machine_behavior           = "SkipUnavailableMachines"
  }

  git_anonymous_persistence_settings {
    url            = "https://github.com/OctopusSolutionsEngineering/OctopusEasyModeMCP.git"
    base_path      = ".octopus/cac_test"
    default_branch = "main"
  }
}
