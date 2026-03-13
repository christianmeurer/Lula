@echo off
setlocal EnableExtensions

for %%I in ("%~dp0..") do set "ROOT=%%~fI"

if /I "%~1"=="/?" goto :usage
if not "%~1"=="" set "AZ_IMAGE_TAG=%~1"

if "%AZ_LOCATION%"=="" set "AZ_LOCATION=eastus"
if "%AZ_CONTAINER_PORT%"=="" set "AZ_CONTAINER_PORT=8001"
if "%AZ_CONTAINER_CPU%"=="" set "AZ_CONTAINER_CPU=1.0"
if "%AZ_CONTAINER_MEMORY%"=="" set "AZ_CONTAINER_MEMORY=2.0Gi"
if "%LG_PROFILE%"=="" set "LG_PROFILE=prod"
if "%AZ_DEPLOY_TARGET%"=="" set "AZ_DEPLOY_TARGET=containerapp"
if "%LG_REMOTE_API_AUTH_MODE%"=="" if not "%LG_REMOTE_API_BEARER_TOKEN%"=="" set "LG_REMOTE_API_AUTH_MODE=bearer"
if "%LG_REMOTE_API_AUTH_MODE%"=="" set "LG_REMOTE_API_AUTH_MODE=off"
if "%LG_REMOTE_API_TRUST_FORWARDED_HEADERS%"=="" (
  if /I "%AZ_DEPLOY_TARGET%"=="containerapp" (
    set "LG_REMOTE_API_TRUST_FORWARDED_HEADERS=true"
  ) else (
    set "LG_REMOTE_API_TRUST_FORWARDED_HEADERS=false"
  )
)

call :require AZ_RESOURCE_GROUP || goto :usage
call :require AZ_ACR_NAME || goto :usage
if /I "%LG_REMOTE_API_AUTH_MODE%"=="bearer" call :require LG_REMOTE_API_BEARER_TOKEN || goto :usage

if /I "%AZ_DEPLOY_TARGET%"=="containerapp" (
  call :require AZ_CONTAINERAPP_NAME || goto :usage
  if "%AZ_CONTAINERAPP_ENV%"=="" set "AZ_CONTAINERAPP_ENV=%AZ_CONTAINERAPP_NAME%-env"
  if "%AZ_IMAGE_NAME%"=="" set "AZ_IMAGE_NAME=%AZ_CONTAINERAPP_NAME%"
) else (
  if /I "%AZ_DEPLOY_TARGET%"=="vm" (
    if "%AZ_VM_NAME%"=="" if not "%AZ_CONTAINERAPP_NAME%"=="" set "AZ_VM_NAME=%AZ_CONTAINERAPP_NAME%"
    if "%AZ_VM_NAME%"=="" set "AZ_VM_NAME=lula-personal-vm"
    if "%AZ_IMAGE_NAME%"=="" set "AZ_IMAGE_NAME=%AZ_VM_NAME%"
    if "%AZ_VM_IMAGE%"=="" set "AZ_VM_IMAGE=Ubuntu2204"
    if "%AZ_VM_ADMIN_USERNAME%"=="" set "AZ_VM_ADMIN_USERNAME=azureuser"
    if "%AZ_VM_SIZE%"=="" set "AZ_VM_SIZE=Standard_D2s_v5"
    if "%AZ_VM_PRIORITY%"=="" set "AZ_VM_PRIORITY=Spot"
    if "%AZ_VM_EVICTION_POLICY%"=="" set "AZ_VM_EVICTION_POLICY=Deallocate"
    if "%AZ_VM_MAX_PRICE%"=="" set "AZ_VM_MAX_PRICE=-1"
    if "%AZ_VM_OS_DISK_SIZE_GB%"=="" set "AZ_VM_OS_DISK_SIZE_GB=64"
    if "%AZ_VM_CONTAINER_NAME%"=="" set "AZ_VM_CONTAINER_NAME=%AZ_VM_NAME%"
  ) else (
    echo Unsupported AZ_DEPLOY_TARGET: %AZ_DEPLOY_TARGET% 1>&2
    goto :usage
  )
)

if "%AZ_IMAGE_TAG%"=="" set "AZ_IMAGE_TAG=latest"

echo [deploy] root: %ROOT%
echo [deploy] target: %AZ_DEPLOY_TARGET%
echo [deploy] resource group: %AZ_RESOURCE_GROUP%
echo [deploy] image: %AZ_IMAGE_NAME%:%AZ_IMAGE_TAG%
call az group create --name "%AZ_RESOURCE_GROUP%" --location "%AZ_LOCATION%" >nul || exit /b 1

call az acr show --name "%AZ_ACR_NAME%" --resource-group "%AZ_RESOURCE_GROUP%" >nul 2>nul
if errorlevel 1 (
  call az acr create --name "%AZ_ACR_NAME%" --resource-group "%AZ_RESOURCE_GROUP%" --location "%AZ_LOCATION%" --sku Basic --admin-enabled true >nul || exit /b 1
) else (
  call az acr update --name "%AZ_ACR_NAME%" --admin-enabled true >nul || exit /b 1
)

call az acr build --registry "%AZ_ACR_NAME%" --image "%AZ_IMAGE_NAME%:%AZ_IMAGE_TAG%" "%ROOT%" || exit /b 1

for /f "usebackq delims=" %%I in (`az acr show --name "%AZ_ACR_NAME%" --query loginServer -o tsv`) do set "AZ_ACR_LOGIN_SERVER=%%I"
for /f "usebackq delims=" %%I in (`az acr credential show --name "%AZ_ACR_NAME%" --query username -o tsv`) do set "AZ_ACR_USERNAME=%%I"
for /f "usebackq delims=" %%I in (`az acr credential show --name "%AZ_ACR_NAME%" --query passwords[0].value -o tsv`) do set "AZ_ACR_PASSWORD=%%I"
set "AZ_IMAGE=%AZ_ACR_LOGIN_SERVER%/%AZ_IMAGE_NAME%:%AZ_IMAGE_TAG%"

if /I "%AZ_DEPLOY_TARGET%"=="containerapp" goto :deploy_containerapp
goto :deploy_vm

:deploy_containerapp
echo [deploy] container app: %AZ_CONTAINERAPP_NAME%

call az extension add --name containerapp --upgrade >nul || exit /b 1
call az provider register --namespace Microsoft.App >nul || exit /b 1
call az provider register --namespace Microsoft.OperationalInsights >nul || exit /b 1

call az containerapp env show --name "%AZ_CONTAINERAPP_ENV%" --resource-group "%AZ_RESOURCE_GROUP%" >nul 2>nul
if errorlevel 1 (
  call az containerapp env create --name "%AZ_CONTAINERAPP_ENV%" --resource-group "%AZ_RESOURCE_GROUP%" --location "%AZ_LOCATION%" >nul || exit /b 1
)

set "ENV_ARGS=LG_PROFILE=%LG_PROFILE% PORT=%AZ_CONTAINER_PORT% LG_REMOTE_API_AUTH_MODE=%LG_REMOTE_API_AUTH_MODE% LG_REMOTE_API_TRUST_FORWARDED_HEADERS=%LG_REMOTE_API_TRUST_FORWARDED_HEADERS%"
if not "%LG_REMOTE_API_BEARER_TOKEN%"=="" set "ENV_ARGS=%ENV_ARGS% LG_REMOTE_API_BEARER_TOKEN=%LG_REMOTE_API_BEARER_TOKEN%"
if not "%LG_RUNNER_API_KEY%"=="" set "ENV_ARGS=%ENV_ARGS% LG_RUNNER_API_KEY=%LG_RUNNER_API_KEY%"
if not "%MODEL_ACCESS_KEY%"=="" set "ENV_ARGS=%ENV_ARGS% MODEL_ACCESS_KEY=%MODEL_ACCESS_KEY%"
if not "%DIGITAL_OCEAN_MODEL_ACCESS_KEY%"=="" set "ENV_ARGS=%ENV_ARGS% DIGITAL_OCEAN_MODEL_ACCESS_KEY=%DIGITAL_OCEAN_MODEL_ACCESS_KEY%"

call az containerapp show --name "%AZ_CONTAINERAPP_NAME%" --resource-group "%AZ_RESOURCE_GROUP%" >nul 2>nul
if errorlevel 1 (
  call az containerapp create --name "%AZ_CONTAINERAPP_NAME%" --resource-group "%AZ_RESOURCE_GROUP%" --environment "%AZ_CONTAINERAPP_ENV%" --image "%AZ_IMAGE%" --ingress external --target-port %AZ_CONTAINER_PORT% --registry-server "%AZ_ACR_LOGIN_SERVER%" --registry-username "%AZ_ACR_USERNAME%" --registry-password "%AZ_ACR_PASSWORD%" --cpu %AZ_CONTAINER_CPU% --memory %AZ_CONTAINER_MEMORY% --env-vars %ENV_ARGS% || exit /b 1
) else (
  call az containerapp registry set --name "%AZ_CONTAINERAPP_NAME%" --resource-group "%AZ_RESOURCE_GROUP%" --server "%AZ_ACR_LOGIN_SERVER%" --username "%AZ_ACR_USERNAME%" --password "%AZ_ACR_PASSWORD%" >nul || exit /b 1
  call az containerapp update --name "%AZ_CONTAINERAPP_NAME%" --resource-group "%AZ_RESOURCE_GROUP%" --image "%AZ_IMAGE%" --cpu %AZ_CONTAINER_CPU% --memory %AZ_CONTAINER_MEMORY% --set-env-vars %ENV_ARGS% || exit /b 1
)

for /f "usebackq delims=" %%I in (`az containerapp show --name "%AZ_CONTAINERAPP_NAME%" --resource-group "%AZ_RESOURCE_GROUP%" --query properties.configuration.ingress.fqdn -o tsv`) do set "AZ_CONTAINERAPP_FQDN=%%I"

if not "%AZ_CONTAINERAPP_FQDN%"=="" echo [deploy] remote api: https://%AZ_CONTAINERAPP_FQDN%

exit /b 0

:deploy_vm
echo [deploy] vm: %AZ_VM_NAME%
echo [deploy] vm recommendation: Standard_D2s_v5 Spot ^(2 vCPU, 8 GiB RAM, 64 GiB OS disk^)
echo [deploy] vm size: %AZ_VM_SIZE%
echo [deploy] vm priority: %AZ_VM_PRIORITY%

call az provider register --namespace Microsoft.Compute >nul || exit /b 1
call az provider register --namespace Microsoft.Network >nul || exit /b 1

call :encode_b64 AZ_ACR_PASSWORD AZ_ACR_PASSWORD_B64 || exit /b 1
call :encode_b64 LG_RUNNER_API_KEY LG_RUNNER_API_KEY_B64 || exit /b 1
call :encode_b64 LG_REMOTE_API_BEARER_TOKEN LG_REMOTE_API_BEARER_TOKEN_B64 || exit /b 1
call :encode_b64 MODEL_ACCESS_KEY MODEL_ACCESS_KEY_B64 || exit /b 1
call :encode_b64 DIGITAL_OCEAN_MODEL_ACCESS_KEY DIGITAL_OCEAN_MODEL_ACCESS_KEY_B64 || exit /b 1

call az vm show --name "%AZ_VM_NAME%" --resource-group "%AZ_RESOURCE_GROUP%" >nul 2>nul
if errorlevel 1 (
  if /I "%AZ_VM_PRIORITY%"=="Spot" (
    if "%AZ_PUBLIC_DNS_LABEL%"=="" (
      call az vm create --resource-group "%AZ_RESOURCE_GROUP%" --name "%AZ_VM_NAME%" --location "%AZ_LOCATION%" --image "%AZ_VM_IMAGE%" --size "%AZ_VM_SIZE%" --admin-username "%AZ_VM_ADMIN_USERNAME%" --authentication-type ssh --generate-ssh-keys --public-ip-sku Standard --storage-sku StandardSSD_LRS --os-disk-size-gb %AZ_VM_OS_DISK_SIZE_GB% --priority Spot --eviction-policy %AZ_VM_EVICTION_POLICY% --max-price %AZ_VM_MAX_PRICE% || exit /b 1
    ) else (
      call az vm create --resource-group "%AZ_RESOURCE_GROUP%" --name "%AZ_VM_NAME%" --location "%AZ_LOCATION%" --image "%AZ_VM_IMAGE%" --size "%AZ_VM_SIZE%" --admin-username "%AZ_VM_ADMIN_USERNAME%" --authentication-type ssh --generate-ssh-keys --public-ip-sku Standard --public-ip-address-dns-name "%AZ_PUBLIC_DNS_LABEL%" --storage-sku StandardSSD_LRS --os-disk-size-gb %AZ_VM_OS_DISK_SIZE_GB% --priority Spot --eviction-policy %AZ_VM_EVICTION_POLICY% --max-price %AZ_VM_MAX_PRICE% || exit /b 1
    )
  ) else (
    if /I not "%AZ_VM_PRIORITY%"=="Regular" (
      echo Unsupported AZ_VM_PRIORITY: %AZ_VM_PRIORITY% 1>&2
      exit /b 1
    )
    if "%AZ_PUBLIC_DNS_LABEL%"=="" (
      call az vm create --resource-group "%AZ_RESOURCE_GROUP%" --name "%AZ_VM_NAME%" --location "%AZ_LOCATION%" --image "%AZ_VM_IMAGE%" --size "%AZ_VM_SIZE%" --admin-username "%AZ_VM_ADMIN_USERNAME%" --authentication-type ssh --generate-ssh-keys --public-ip-sku Standard --storage-sku StandardSSD_LRS --os-disk-size-gb %AZ_VM_OS_DISK_SIZE_GB% || exit /b 1
    ) else (
      call az vm create --resource-group "%AZ_RESOURCE_GROUP%" --name "%AZ_VM_NAME%" --location "%AZ_LOCATION%" --image "%AZ_VM_IMAGE%" --size "%AZ_VM_SIZE%" --admin-username "%AZ_VM_ADMIN_USERNAME%" --authentication-type ssh --generate-ssh-keys --public-ip-sku Standard --public-ip-address-dns-name "%AZ_PUBLIC_DNS_LABEL%" --storage-sku StandardSSD_LRS --os-disk-size-gb %AZ_VM_OS_DISK_SIZE_GB% || exit /b 1
    )
  )
)

call az vm start --resource-group "%AZ_RESOURCE_GROUP%" --name "%AZ_VM_NAME%" >nul || exit /b 1
call az vm open-port --resource-group "%AZ_RESOURCE_GROUP%" --name "%AZ_VM_NAME%" --port %AZ_CONTAINER_PORT% --priority 1100 >nul 2>nul

call :write_vm_setup_script || exit /b 1
call az vm run-command invoke --resource-group "%AZ_RESOURCE_GROUP%" --name "%AZ_VM_NAME%" --command-id RunShellScript --scripts @%AZ_VM_SETUP_FILE% >nul
set "AZ_VM_RUN_COMMAND_EXIT=%ERRORLEVEL%"
del /q "%AZ_VM_SETUP_FILE%" >nul 2>nul
if not "%AZ_VM_RUN_COMMAND_EXIT%"=="0" exit /b %AZ_VM_RUN_COMMAND_EXIT%

for /f "usebackq delims=" %%I in (`az vm show -d --name "%AZ_VM_NAME%" --resource-group "%AZ_RESOURCE_GROUP%" --query publicIps -o tsv`) do set "AZ_VM_PUBLIC_IP=%%I"
for /f "usebackq delims=" %%I in (`az vm show -d --name "%AZ_VM_NAME%" --resource-group "%AZ_RESOURCE_GROUP%" --query fqdns -o tsv`) do set "AZ_VM_FQDN=%%I"

if not "%AZ_VM_FQDN%"=="" (
  echo [deploy] remote api: http://%AZ_VM_FQDN%:%AZ_CONTAINER_PORT%
) else (
  if not "%AZ_VM_PUBLIC_IP%"=="" echo [deploy] remote api: http://%AZ_VM_PUBLIC_IP%:%AZ_CONTAINER_PORT%
)
echo [deploy] warning: VM mode exposes HTTP directly; add TLS and access control before public internet use.

exit /b 0

:require
if defined %~1 exit /b 0
echo Missing environment variable: %~1 1>&2
exit /b 1

:encode_b64
if not defined %~1 (
  set "%~2="
  exit /b 0
)
set "%~2="
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([Environment]::GetEnvironmentVariable('%~1')))"`) do set "%~2=%%I"
if defined %~2 exit /b 0
exit /b 1

:write_vm_setup_script
set "AZ_VM_SETUP_FILE=%TEMP%\%AZ_VM_NAME%-deploy.sh"
>"%AZ_VM_SETUP_FILE%" echo #!/usr/bin/env sh
>>"%AZ_VM_SETUP_FILE%" echo set -eu
>>"%AZ_VM_SETUP_FILE%" echo APP_NAME="%AZ_VM_CONTAINER_NAME%"
>>"%AZ_VM_SETUP_FILE%" echo APP_PORT="%AZ_CONTAINER_PORT%"
>>"%AZ_VM_SETUP_FILE%" echo ACR_LOGIN_SERVER="%AZ_ACR_LOGIN_SERVER%"
>>"%AZ_VM_SETUP_FILE%" echo ACR_USERNAME="%AZ_ACR_USERNAME%"
>>"%AZ_VM_SETUP_FILE%" echo ACR_PASSWORD="$(printf '%%s' '%AZ_ACR_PASSWORD_B64%' ^| base64 -d)"
>>"%AZ_VM_SETUP_FILE%" echo IMAGE="%AZ_IMAGE%"
>>"%AZ_VM_SETUP_FILE%" echo LG_PROFILE="%LG_PROFILE%"
>>"%AZ_VM_SETUP_FILE%" echo LG_REMOTE_API_AUTH_MODE="%LG_REMOTE_API_AUTH_MODE%"
>>"%AZ_VM_SETUP_FILE%" echo LG_REMOTE_API_BEARER_TOKEN="$(printf '%%s' '%LG_REMOTE_API_BEARER_TOKEN_B64%' ^| base64 -d)"
>>"%AZ_VM_SETUP_FILE%" echo LG_REMOTE_API_TRUST_FORWARDED_HEADERS="%LG_REMOTE_API_TRUST_FORWARDED_HEADERS%"
>>"%AZ_VM_SETUP_FILE%" echo LG_RUNNER_API_KEY="$(printf '%%s' '%LG_RUNNER_API_KEY_B64%' ^| base64 -d)"
>>"%AZ_VM_SETUP_FILE%" echo MODEL_ACCESS_KEY="$(printf '%%s' '%MODEL_ACCESS_KEY_B64%' ^| base64 -d)"
>>"%AZ_VM_SETUP_FILE%" echo DIGITAL_OCEAN_MODEL_ACCESS_KEY="$(printf '%%s' '%DIGITAL_OCEAN_MODEL_ACCESS_KEY_B64%' ^| base64 -d)"
>>"%AZ_VM_SETUP_FILE%" echo sudo apt-get update
>>"%AZ_VM_SETUP_FILE%" echo sudo apt-get install -y ca-certificates curl docker.io
>>"%AZ_VM_SETUP_FILE%" echo sudo systemctl enable docker
>>"%AZ_VM_SETUP_FILE%" echo sudo systemctl start docker
>>"%AZ_VM_SETUP_FILE%" echo printf '%%s' "$ACR_PASSWORD" ^| sudo docker login "$ACR_LOGIN_SERVER" --username "$ACR_USERNAME" --password-stdin
>>"%AZ_VM_SETUP_FILE%" echo sudo docker pull "$IMAGE"
>>"%AZ_VM_SETUP_FILE%" echo sudo docker rm -f "$APP_NAME" ^>/dev/null 2^>^&1 ^|^| true
>>"%AZ_VM_SETUP_FILE%" echo set -- sudo docker run -d --name "$APP_NAME" --restart unless-stopped -p "$APP_PORT:$APP_PORT" -e "LG_PROFILE=$LG_PROFILE" -e "PORT=$APP_PORT" -e "LG_REMOTE_API_AUTH_MODE=$LG_REMOTE_API_AUTH_MODE" -e "LG_REMOTE_API_TRUST_FORWARDED_HEADERS=$LG_REMOTE_API_TRUST_FORWARDED_HEADERS"
>>"%AZ_VM_SETUP_FILE%" echo if [ -n "$LG_REMOTE_API_BEARER_TOKEN" ]; then set -- "$@" -e "LG_REMOTE_API_BEARER_TOKEN=$LG_REMOTE_API_BEARER_TOKEN"; fi
>>"%AZ_VM_SETUP_FILE%" echo if [ -n "$LG_RUNNER_API_KEY" ]; then set -- "$@" -e "LG_RUNNER_API_KEY=$LG_RUNNER_API_KEY"; fi
>>"%AZ_VM_SETUP_FILE%" echo if [ -n "$MODEL_ACCESS_KEY" ]; then set -- "$@" -e "MODEL_ACCESS_KEY=$MODEL_ACCESS_KEY"; fi
>>"%AZ_VM_SETUP_FILE%" echo if [ -n "$DIGITAL_OCEAN_MODEL_ACCESS_KEY" ]; then set -- "$@" -e "DIGITAL_OCEAN_MODEL_ACCESS_KEY=$DIGITAL_OCEAN_MODEL_ACCESS_KEY"; fi
>>"%AZ_VM_SETUP_FILE%" echo set -- "$@" "$IMAGE"
>>"%AZ_VM_SETUP_FILE%" echo "$@"
>>"%AZ_VM_SETUP_FILE%" echo i=0
>>"%AZ_VM_SETUP_FILE%" echo while [ "$i" -lt 60 ]; do
>>"%AZ_VM_SETUP_FILE%" echo   if curl -fsS "http://127.0.0.1:$APP_PORT/healthz" ^>/dev/null; then
>>"%AZ_VM_SETUP_FILE%" echo     exit 0
>>"%AZ_VM_SETUP_FILE%" echo   fi
>>"%AZ_VM_SETUP_FILE%" echo   i=$((i + 1))
>>"%AZ_VM_SETUP_FILE%" echo   sleep 2
>>"%AZ_VM_SETUP_FILE%" echo done
>>"%AZ_VM_SETUP_FILE%" echo echo "remote api did not become healthy on port $APP_PORT" 1^>^&2
>>"%AZ_VM_SETUP_FILE%" echo sudo docker logs "$APP_NAME" --tail 200 ^|^| true
>>"%AZ_VM_SETUP_FILE%" echo exit 1
exit /b 0

:usage
echo Usage: scripts\azure_deploy_personal.cmd [image-tag]
echo Required environment variables: AZ_RESOURCE_GROUP, AZ_ACR_NAME
echo Optional environment variables: AZ_DEPLOY_TARGET, AZ_IMAGE_NAME, AZ_IMAGE_TAG, AZ_LOCATION, AZ_CONTAINER_PORT, LG_PROFILE, LG_RUNNER_API_KEY, LG_REMOTE_API_AUTH_MODE, LG_REMOTE_API_BEARER_TOKEN, LG_REMOTE_API_TRUST_FORWARDED_HEADERS, MODEL_ACCESS_KEY, DIGITAL_OCEAN_MODEL_ACCESS_KEY
echo Container Apps variables: AZ_CONTAINERAPP_NAME, AZ_CONTAINERAPP_ENV, AZ_CONTAINER_CPU, AZ_CONTAINER_MEMORY
echo VM variables: AZ_VM_NAME, AZ_VM_SIZE, AZ_VM_PRIORITY, AZ_VM_EVICTION_POLICY, AZ_VM_MAX_PRICE, AZ_VM_OS_DISK_SIZE_GB, AZ_VM_IMAGE, AZ_VM_ADMIN_USERNAME, AZ_VM_CONTAINER_NAME, AZ_PUBLIC_DNS_LABEL
echo Hardened default: AZ_DEPLOY_TARGET=containerapp
echo Lab VM defaults: AZ_DEPLOY_TARGET=vm, AZ_VM_SIZE=Standard_D2s_v5, AZ_VM_PRIORITY=Spot, AZ_VM_OS_DISK_SIZE_GB=64
exit /b 1
