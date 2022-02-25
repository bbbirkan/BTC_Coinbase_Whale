# BTC_Coinbase_Whale

SOURCE=https://github.com/pmaji/crypto-whale-watching-app 


Create project in https://cloud.google.com/
Download from sdk https://cloud.google.com/sdk/docs/install-sdk 

In terminal:
/Users/YOURFOLDERNAME/PycharmProjects/google-cloud-sdk/install.sh


Then follow the setup section run this in terminal
gcloud init 

gcloud builds submit --tag gcr.io/ProjectID/dashtest  --project=ProjectID
gcloud run deploy --image gcr.io/ProjectID/dashtest --platform managed  --project=ProjectID 
