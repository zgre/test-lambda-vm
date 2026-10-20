from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

ROOT = Path(__file__).resolve().parents[2]
LOCAL_DEV_URL = "http://localhost:5173"


class TimeTrackerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- data ---------------------------------------------------------
        table = dynamodb.Table(
            self, "Table",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            # Keep time data if the stack is deleted.
            removal_policy=RemovalPolicy.RETAIN,
        )
        table.add_local_secondary_index(
            index_name="byStart",
            sort_key=dynamodb.Attribute(name="startedAt", type=dynamodb.AttributeType.STRING),
        )

        # ---- frontend hosting --------------------------------------------
        site_bucket = s3.Bucket(
            self, "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        distribution = cloudfront.Distribution(
            self, "Site",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            # Single-page app: unknown paths fall back to index.html.
            error_responses=[
                cloudfront.ErrorResponse(http_status=code, response_http_status=200,
                                         response_page_path="/index.html", ttl=Duration.seconds(0))
                for code in (403, 404)
            ],
        )
        site_url = f"https://{distribution.distribution_domain_name}"

        # ---- auth ---------------------------------------------------------
        user_pool = cognito.UserPool(
            self, "Users",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(min_length=10),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN,
        )
        domain = user_pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=f"timetracker-{self.account}"),
        )
        client = user_pool.add_client(
            "WebClient",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=[f"{site_url}/", f"{LOCAL_DEV_URL}/"],
                logout_urls=[f"{site_url}/", f"{LOCAL_DEV_URL}/"],
            ),
            access_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
        )

        # ---- API ----------------------------------------------------------
        fn = lambda_.Function(
            self, "ApiFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="app.handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(ROOT / "backend"),
                exclude=[".venv", "tests", "**/__pycache__", ".pytest_cache", "requirements-dev.txt"],
            ),
            memory_size=256,
            timeout=Duration.seconds(10),
            environment={"TABLE_NAME": table.table_name},
            log_group=logs.LogGroup(self, "ApiLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        table.grant_read_write_data(fn)

        api = apigw.HttpApi(
            self, "Api",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=[site_url, LOCAL_DEV_URL],
                allow_methods=[apigw.CorsHttpMethod.ANY],
                allow_headers=["authorization", "content-type"],
                max_age=Duration.hours(1),
            ),
            default_authorizer=authorizers.HttpJwtAuthorizer(
                "JwtAuth",
                jwt_issuer=f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool.user_pool_id}",
                jwt_audience=[client.user_pool_client_id],
            ),
        )
        integration = integrations.HttpLambdaIntegration("ApiIntegration", fn)
        routes = {
            "/projects": [apigw.HttpMethod.GET, apigw.HttpMethod.POST],
            "/projects/{id}": [apigw.HttpMethod.PATCH, apigw.HttpMethod.DELETE],
            "/entries": [apigw.HttpMethod.GET, apigw.HttpMethod.POST],
            "/entries/{id}": [apigw.HttpMethod.PATCH, apigw.HttpMethod.DELETE],
            "/timer": [apigw.HttpMethod.GET],
            "/timer/start": [apigw.HttpMethod.POST],
            "/timer/stop": [apigw.HttpMethod.POST],
            "/reports/summary": [apigw.HttpMethod.GET],
        }
        for path, methods in routes.items():
            api.add_routes(path=path, methods=methods, integration=integration)

        # ---- deploy the built frontend plus runtime config ------------------
        config = {
            "apiUrl": api.api_endpoint,
            "region": self.region,
            "userPoolClientId": client.user_pool_client_id,
            "authDomain": domain.base_url(),
        }
        s3deploy.BucketDeployment(
            self, "DeploySite",
            destination_bucket=site_bucket,
            sources=[
                s3deploy.Source.asset(str(ROOT / "frontend" / "dist")),
                s3deploy.Source.json_data("config.json", config),
            ],
            distribution=distribution,
            distribution_paths=["/*"],
        )

        CfnOutput(self, "SiteUrl", value=site_url)
        CfnOutput(self, "ApiUrl", value=api.api_endpoint)
        CfnOutput(self, "UserPoolClientId", value=client.user_pool_client_id)
        CfnOutput(self, "AuthDomain", value=config["authDomain"])
