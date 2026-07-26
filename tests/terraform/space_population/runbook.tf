resource "octopusdeploy_runbook" "backup_database" {
  project_id         = octopusdeploy_project.test_project.id
  name               = "Backup Database"
  description        = "Backs up the database"
  multi_tenancy_mode = "Untenanted"

  connectivity_policy {
    allow_deployments_to_no_targets = true
    exclude_unhealthy_targets       = false
    skip_machine_behavior           = "SkipUnavailableMachines"
  }

  retention_policy {
    quantity_to_keep = 10
  }

  environment_scope           = "Specified"
  environments                = [octopusdeploy_environment.development.id]
  default_guided_failure_mode = "EnvironmentDefault"
  force_package_download      = false
}

resource "octopusdeploy_runbook_process" "backup_database" {
  runbook_id = octopusdeploy_runbook.backup_database.id

  step {
    condition           = "Success"
    name                = "Run Script"
    package_requirement = "LetOctopusDecide"
    start_trigger       = "StartAfterPrevious"

    action {
      action_type    = "Octopus.Script"
      name           = "Run Script"
      condition      = "Success"
      run_on_server  = true
      is_disabled    = false
      is_required    = true
      worker_pool_id = ""

      properties = {
        "Octopus.Action.Script.ScriptSource" = "Inline"
        "Octopus.Action.Script.ScriptBody"   = "echo 'Backing up database'"
        "Octopus.Action.Script.Syntax"       = "Bash"
      }

      environments          = []
      excluded_environments = []
      channels              = []
      tenant_tags           = []
    }

    properties   = {}
    target_roles = []
  }
}

resource "octopusdeploy_runbook" "deploy_service" {
  project_id         = octopusdeploy_project.test_project.id
  name               = "Deploy Service"
  description        = "Deploys the service"
  multi_tenancy_mode = "Untenanted"

  connectivity_policy {
    allow_deployments_to_no_targets = true
    exclude_unhealthy_targets       = false
    skip_machine_behavior           = "SkipUnavailableMachines"
  }

  retention_policy {
    quantity_to_keep = 10
  }

  environment_scope           = "Specified"
  environments                = [octopusdeploy_environment.development.id, octopusdeploy_environment.production.id]
  default_guided_failure_mode = "EnvironmentDefault"
  force_package_download      = false
}

resource "octopusdeploy_runbook_process" "deploy_service" {
  runbook_id = octopusdeploy_runbook.deploy_service.id

  step {
    condition           = "Success"
    name                = "Deploy"
    package_requirement = "LetOctopusDecide"
    start_trigger       = "StartAfterPrevious"

    action {
      action_type    = "Octopus.Script"
      name           = "Deploy"
      condition      = "Success"
      run_on_server  = true
      is_disabled    = false
      is_required    = true
      worker_pool_id = ""

      properties = {
        "Octopus.Action.Script.ScriptSource" = "Inline"
        "Octopus.Action.Script.ScriptBody"   = "echo 'Deploying service'"
        "Octopus.Action.Script.Syntax"       = "Bash"
      }

      environments          = []
      excluded_environments = []
      channels              = []
      tenant_tags           = []
    }

    properties   = {}
    target_roles = []
  }
}
