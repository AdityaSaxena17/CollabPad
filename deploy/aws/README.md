# CollabPad AWS demo

Use an EC2 instance in `ap-south-1` with Docker Compose v2, encrypted EBS, and Session Manager access. Point a DNS hostname at its public IP and allow inbound TCP 80 and 443 for Caddy. Require IMDSv2 with response hop limit 2 for the Bedrock container.

Attach an EC2 instance profile that permits Session Manager and the `bedrock:InvokeModel` action on the APAC Nova Micro and Nova Lite inference profiles and their destination models. The stack scopes access to those two profiles.

The [CloudFormation template](infrastructure.yml) creates the VPC, host, Elastic IP, private release bucket, and instance profile. From the repository root, install dependencies with `npm ci`, configure the Clerk keys in `.env`, and sign in to AWS CLI. The script also needs Bash, Git, and Python 3.

```bash
./deploy/aws/collabpad.sh deploy
./deploy/aws/collabpad.sh destroy
```

Deploy prints the Elastic IP before building the app. Point the GoDaddy `collabpad` A record at that IP and configure Clerk for `collabpad.mukthish.co.in`. Point Clerk webhooks at `https://collabpad.mukthish.co.in/api/webhooks/clerk`. Re-running deploy preserves the runtime settings in SSM and the data on the instance. Destroy removes the AWS stack, the root disk and its Raft and database data, the release archive, and the runtime settings. Remove the GoDaddy A record separately.

Verify HTTPS sign-in and collaboration from two browser profiles. Exercise autocomplete, summaries, enhancement, and grammar correction with sample text. Kill the elected Raft leader, verify that the browser reconnects and saves edits, then restart that replica.
