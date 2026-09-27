# Tear down every DeepTrace resource in AWS.
#
#   .\teardown-aws.ps1            asks for confirmation, then deletes everything
#   .\teardown-aws.ps1 -Force     no prompt (for scripts)
#
# Order matters. Consumers stop before the things that feed them, the database goes before
# its subnet group, IAM roles go last (deleting a role still in use fails), and the security
# groups go after everything that references them. Each step tolerates "already gone" so a
# half-finished run can simply be run again.
#
# It deliberately does NOT touch the account's own access keys, the [default] CLI profile,
# or an IAM user you created for yourself. Those are not DeepTrace resources.

param([switch]$Force)

$ErrorActionPreference = "Continue"
$aws = "$env:LOCALAPPDATA\Programs\Amazon\AWSCLIV2\aws.exe"

$REGION    = "ap-south-1"
$ACCOUNT   = "838882524724"
$CLUSTER   = "deeptrace"
$BUCKET    = "deeptrace-838882524724"
$QUEUE     = "deeptrace-jobs"
$DB        = "deeptrace-pg"
$SUBNETGRP = "deeptrace-db-subnets"
$ECRREPO   = "deeptrace"
$SECRET    = "deeptrace/app"
$LOGGROUP  = "/ecs/deeptrace"
$DASHBOARD = "deeptrace"
$BUDGET    = "deeptrace-monthly"
$TOPIC     = "deeptrace-alerts"
$ALB       = "deeptrace-alb"
$TG        = "deeptrace-api"
$SGS       = @("deeptrace-alb", "deeptrace-tasks", "deeptrace-rds")

function Say($m) { Write-Host "  $m" }
function Aws($a) { & $aws @a 2>&1 | Out-Null }

if (-not $Force) {
  Write-Host ""
  Write-Host "This DELETES the DeepTrace stack in ${REGION}:" -ForegroundColor Yellow
  Write-Host "  ECS services + cluster, ALB, RDS $DB (DATA IS LOST), S3 $BUCKET,"
  Write-Host "  SQS $QUEUE, ECR images, the secret, alarms, dashboard, log group."
  Write-Host ""
  $answer = Read-Host "Type 'delete' to continue"
  if ($answer -ne "delete") { Write-Host "nothing was deleted"; exit 0 }
}

Write-Host "`n=== 1. autoscaling and ECS services ===" -ForegroundColor Cyan
foreach ($svc in "deeptrace-api", "deeptrace-worker") {
  Aws @("application-autoscaling", "deregister-scalable-target", "--service-namespace", "ecs",
        "--resource-id", "service/$CLUSTER/$svc", "--scalable-dimension", "ecs:service:DesiredCount")
  Say "deregistered scaling for $svc"
  Aws @("ecs", "update-service", "--cluster", $CLUSTER, "--service", $svc, "--desired-count", "0")
  Say "scaled $svc to 0"
}
foreach ($svc in "deeptrace-api", "deeptrace-worker") {
  Aws @("ecs", "delete-service", "--cluster", $CLUSTER, "--service", $svc, "--force")
  Say "deleted service $svc"
}

Write-Host "=== 2. load balancer ===" -ForegroundColor Cyan
$albArn = (& $aws elbv2 describe-load-balancers --names $ALB --region $REGION `
  --query "LoadBalancers[0].LoadBalancerArn" --output text 2>$null)
if ($albArn -and $albArn -ne "None") {
  Aws @("elbv2", "delete-load-balancer", "--load-balancer-arn", $albArn, "--region", $REGION)
  Say "deleting ALB $ALB - waiting for it to disappear"
  # polled rather than using a waiter, so this does not depend on a waiter name existing
  for ($i = 0; $i -lt 40; $i++) {
    $still = (& $aws elbv2 describe-load-balancers --names $ALB --region $REGION `
      --query "LoadBalancers[0].LoadBalancerArn" --output text 2>$null)
    if (-not $still -or $still -eq "None") { break }
    Start-Sleep -Seconds 10
  }
  Say "ALB gone"
} else { Say "ALB already gone" }
$tgArn = (& $aws elbv2 describe-target-groups --names $TG --region $REGION `
  --query "TargetGroups[0].TargetGroupArn" --output text 2>$null)
if ($tgArn -and $tgArn -ne "None") {
  Aws @("elbv2", "delete-target-group", "--target-group-arn", $tgArn, "--region", $REGION)
  Say "deleted target group $TG"
} else { Say "target group already gone" }

Write-Host "=== 3. database (all rows are lost) ===" -ForegroundColor Cyan
Aws @("rds", "delete-db-instance", "--db-instance-identifier", $DB,
      "--skip-final-snapshot", "--delete-automated-backups", "--region", $REGION)
Say "deleting $DB - this takes several minutes"
& $aws rds wait db-instance-deleted --db-instance-identifier $DB --region $REGION 2>$null
Say "database gone"
Aws @("rds", "delete-db-subnet-group", "--db-subnet-group-name", $SUBNETGRP, "--region", $REGION)
Say "deleted subnet group"

Write-Host "=== 4. secret, queue, bucket, images ===" -ForegroundColor Cyan
Aws @("secretsmanager", "delete-secret", "--secret-id", $SECRET,
      "--force-delete-without-recovery", "--region", $REGION)
Say "deleted secret $SECRET"
Aws @("sqs", "delete-queue", "--queue-url", "https://sqs.$REGION.amazonaws.com/$ACCOUNT/$QUEUE",
      "--region", $REGION)
Say "deleted queue $QUEUE"

# an S3 bucket must be empty before it can be deleted
& $aws s3 rm "s3://$BUCKET" --recursive --region $REGION 2>&1 | Out-Null
Aws @("s3api", "delete-bucket", "--bucket", $BUCKET, "--region", $REGION)
Say "emptied and deleted bucket $BUCKET"
Aws @("ecr", "delete-repository", "--repository-name", $ECRREPO, "--force", "--region", $REGION)
Say "deleted ECR repository and its images"

Write-Host "=== 5. monitoring and alerts ===" -ForegroundColor Cyan
$alarmNames = (& $aws cloudwatch describe-alarms --alarm-name-prefix "deeptrace" --region $REGION `
  --query "MetricAlarms[].AlarmName" --output text 2>$null)
# build one argument array: passing "-join" style concatenation here silently sent the
# command with no alarm names at all, which is why the alarms survived a "successful" run
$alarmList = @($alarmNames -split "\s+" | Where-Object { $_ -and $_ -ne "None" })
if ($alarmList.Count -gt 0) {
  Aws (@("cloudwatch", "delete-alarms", "--alarm-names") + $alarmList + @("--region", $REGION))
  Say "deleted $($alarmList.Count) alarm(s)"
}
Aws @("cloudwatch", "delete-dashboards", "--dashboard-names", $DASHBOARD, "--region", $REGION)
Say "deleted dashboard $DASHBOARD"
Aws @("logs", "delete-log-group", "--log-group-name", $LOGGROUP, "--region", $REGION)
Say "deleted log group $LOGGROUP"
Aws @("budgets", "delete-budget", "--account-id", $ACCOUNT, "--budget-name", $BUDGET)
Say "deleted budget $BUDGET"
$topicArn = (& $aws sns list-topics --region $REGION --query "Topics[?ends_with(TopicArn, '$TOPIC')].TopicArn" --output text 2>$null)
if ($topicArn -and $topicArn -ne "None") {
  Aws @("sns", "delete-topic", "--topic-arn", $topicArn, "--region", $REGION)
  Say "deleted SNS topic"
}

Write-Host "=== 6. IAM roles ===" -ForegroundColor Cyan
foreach ($role in "deeptrace-task", "deeptrace-task-execution") {
  $policies = (& $aws iam list-role-policies --role-name $role --query "PolicyNames[]" --output text 2>$null)
  foreach ($p in ($policies -split "\s+")) {
    if ($p -and $p -ne "None") {
      Aws @("iam", "delete-role-policy", "--role-name", $role, "--policy-name", $p)
    }
  }
  Aws @("iam", "delete-role", "--role-name", $role)
  Say "deleted role $role"
}

Write-Host "=== 7. networking ===" -ForegroundColor Cyan
foreach ($name in $SGS.Keys) {
  $sg = (& $aws ec2 describe-security-groups --filters "Name=group-name,Values=$name" `
    "Name=vpc-id,Values=vpc-0cd011da8ec95bca4" --region $REGION --query "SecurityGroups[0].GroupId" --output text 2>$null)
  if ($sg -and $sg -ne "None") {
    Aws @("ec2", "delete-security-group", "--group-id", $sg, "--region", $REGION)
    Say "deleted security group $name"
  }
}

Write-Host "=== 8. cluster ===" -ForegroundColor Cyan
Aws @("ecs", "delete-cluster", "--cluster", $CLUSTER, "--region", $REGION)
Say "deleted cluster $CLUSTER"

Write-Host "`n=== what is left ===" -ForegroundColor Cyan
$leftBuckets  = (& $aws s3api list-buckets --query "Buckets[].Name" --output text 2>$null)
$leftDbs      = (& $aws rds describe-db-instances --region $REGION --query "DBInstances[].DBInstanceIdentifier" --output text 2>$null)
$leftServices = (& $aws ecs list-services --cluster $CLUSTER --region $REGION --query "serviceArns[]" --output text 2>$null)
$leftImages   = (& $aws ecr list-images --repository-name $ECRREPO --region $REGION --query "imageIds[].imageTag" --output text 2>$null)
Write-Host "S3 buckets   : $leftBuckets"
Write-Host "RDS          : $leftDbs"
Write-Host "ECS services : $leftServices"
Write-Host "ECR images   : $leftImages"
Write-Host "`nAnything still listed above still costs money. Run this script again if a step failed." -ForegroundColor Yellow
Write-Host "The ALB is the slowest to disappear; if it still shows, wait a minute and re-run." -ForegroundColor Yellow
