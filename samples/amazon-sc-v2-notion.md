*Last updated: June 29, 2026*
## Overview
This guide explains how to integrate your Amazon account with Synctify OMS.
Depending on your account type, different connection methods are available:
- **Amazon Seller Central (SC)**
	- Amazon Selling Partner Appstore
	- Synctify OMS (supports multi-region and multi-account setup)
- **Amazon Vendor Central (VC)**
	- Synctify OMS only (supports multi-region and multi-account setup)
## **Installing from the Amazon Selling Partner Appstore**
This method applies to **Amazon Seller Central accounts only**.
### Step 1. Open the Appstore Link
Visit our app on the Amazon Selling Partner Appstore via the following link:
👉 [**Amazon Appstore – Synctify OMS**](https://sellercentral.amazon.com/selling-partner-appstore/dp/amzn1.sp.solution.7e29f7eb-b4a3-45b2-85ae-6970b29d5ce1)
![Navigate to the Amazon Appstore and open the Synctify OMS app page.](https://assets.synctify.net/support/2025/09/28175459/amazon-appstore-open-link-1024x469.png)
### Step 2. Authorize Synctify OMS
Click `Authorize Now`.
- If you are not signed in, log in with your Amazon Seller account.
- Make sure you are using the `Owner Account` to complete this step.
![Click Authorize to proceed with Amazon account connection.](https://assets.synctify.net/support/2025/09/28175627/amazon-appstore-authorize-now-237x300.png)
### Step 3. Grant Permissions
Click `Authorize` after reviewing and consenting to the requested permissions.
![Review the requested permissions and click Authorize to continue.](https://assets.synctify.net/support/2025/09/28175822/amazon-appstore-grant-permissions-792x1024.png)
### Step 4. Configure Fulfillment Location
From the dropdown menu, choose a `Fulfillment Location` and map it to the warehouse you created in Synctify OMS. This ensures your inventory data is synced to the correct location.
If you have one or more warehouses, click `Add` to map additional ones.
![Select a fulfillment location and map it to the warehouse in Synctify OMS.](https://assets.synctify.net/support/2025/09/07151504/amazon-appstore-fulfillment-location-1024x898.png)
After completing the above steps, your Amazon account will be connected.
<callout icon="/icons/checkmark-square_green.svg" color="green_bg">
	Once completed, your Amazon account is successfully connected to [**Synctify OMS**](https://synctify.net/).
</callout>
## Installing from Synctify OMS
You can also connect your Amazon account directly from Synctify OMS, which is useful for managing multiple regions or accounts.
This method supports both **Seller Central and Vendor Central accounts**.
### Step 1. Navigate to Integrations
Navigate to the `Integrations > Connect`, and search for **Amazon**.
You will see available integration types:
- Amazon Seller Central
- Amazon Vendor Direct Fulfillment
- Amazon FBA
- Amazon Vendor Retail Procurement
- Amazon Multi Channel Fulfillment
![From your Synctify OMS dashboard, go to the left-hand menu and click on Integrations > Connect.](https://prod-files-secure.s3.us-west-2.amazonaws.com/notion/amazon-seller-central-integration-guide-connect-integration.png)
![Use the search bar to find "Amazon" and view the available integration types, such as Seller Central, Vendor Direct Fulfillment, FBA, and Vendor Retail Procurement.](https://prod-files-secure.s3.us-west-2.amazonaws.com/notion/amazon-seller-central-integration-guide-search-amazon.png)
### Step 2. Select Integration Type
Choose the integration type you want and click `Connect`.
![Choose the specific integration type you need (e.g., Amazon Seller Central) and click the Connect button.](https://prod-files-secure.s3.us-west-2.amazonaws.com/notion/amazon-seller-central-integration-guide-click-connect.png)
### Step 3. Select Region and Marketplace
In the authorization modal:
- Choose **Marketplace Region**:
	- North America
	- Europe
	- Far East
- Select the specific marketplace based on the region
![In the authorization modal, choose your Marketplace Region (North America, Europe, or Far East) and select your specific marketplace from the dropdown menu.](https://prod-files-secure.s3.us-west-2.amazonaws.com/notion/amazon-seller-central-integration-guide-select-region-and-marketplace.png)
### Step 4. Authorize Amazon Account
Click `Authorize` to proceed.
You will be redirected to Amazon:
- Log in to your Amazon account. Make sure you are using the `Owner Account`
- Follow the authorization steps
![Click the Authorize button to proceed with the connection.](https://prod-files-secure.s3.us-west-2.amazonaws.com/notion/amazon-seller-central-integration-guide-authorize-amazon-account.png)
![After clicking authorize, you will be redirected to the Amazon sign-in page. Log in to your account and follow the remaining on-screen steps to complete the integration.](https://prod-files-secure.s3.us-west-2.amazonaws.com/notion/amazon-seller-central-integration-guide-login-amazon-account.png)
### Step 5. Complete Integration
After completing authorization, your Amazon account will be connected to Synctify OMS.
You can now manage your integration from the OMS dashboard.
<callout icon="/icons/checkmark-square_green.svg" color="green_bg">
	Once completed, your Amazon account is successfully connected to [**Synctify OMS**](https://synctify.net/).
</callout>
## Notes
- Seller Central accounts can be connected via Appstore or OMS
- Vendor Central accounts can only be connected via Synctify OMS
- Ensure correct region and marketplace selection during setup
- Incorrect region or marketplace selection may result in orders or inventory being assigned to the wrong location.
<callout icon="/icons/info-alternate_lightgray.svg" color="gray_bg">
	**Learn More**
	If you're exploring advanced Amazon integration use cases, you may also find these resources helpful:
	- [Amazon Vendor Central Integration Explained](https://www.synctify.net/2025/05/14/amazon-vendor-central-integration-explained-automate-po-orders-asn-invoices-more/)
	- [How to Automate Amazon FBM Orders](https://www.synctify.net/2025/05/13/still-managing-amazon-fbm-orders-manually-heres-how-to-automate-everything/)
</callout>
## FAQs
### 1. Can I connect both Amazon Seller Central and Vendor Central accounts?
Yes. Synctify OMS supports both Seller Central and Vendor Central integrations.
However, Vendor Central accounts can only be connected via Synctify OMS, while Seller Central accounts can be connected via either the Appstore or Synctify OMS.
---
### 2. Can I connect multiple Amazon accounts or marketplaces?
Yes. You can authorize multiple accounts and marketplaces within Synctify OMS and manage them separately.
---
### 3. Do I need to re-authorize my account for Amazon FBA?
No. If your account is already authorized, you can reuse the existing connection for Amazon FBA without re-authorizing.
