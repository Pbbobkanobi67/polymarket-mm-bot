"""
Generate Polymarket API Credentials

Run this script with your private key to generate API credentials.
"""
import os
import sys

# First install the client if needed
try:
    from py_clob_client.client import ClobClient
except ImportError:
    print("Installing py-clob-client...")
    os.system("pip install py-clob-client")
    from py_clob_client.client import ClobClient


def generate_credentials(private_key: str, funder_address: str = None):
    """Generate API credentials from private key"""

    host = "https://clob.polymarket.com"
    chain_id = 137  # Polygon Mainnet

    # Remove 0x prefix if present
    if private_key.startswith("0x"):
        private_key = private_key[2:]

    try:
        if funder_address:
            # For email/Magic wallet users
            client = ClobClient(
                host,
                key=private_key,
                chain_id=chain_id,
                signature_type=1,
                funder=funder_address
            )
        else:
            # For standard Web3 wallet users
            client = ClobClient(
                host,
                key=private_key,
                chain_id=chain_id
            )

        # Generate or derive API credentials
        print("\nGenerating API credentials...")
        api_creds = client.create_or_derive_api_creds()

        print("\n" + "="*60)
        print("YOUR POLYMARKET API CREDENTIALS")
        print("="*60)
        print(f"\nAPI Key:      {api_creds.api_key}")
        print(f"API Secret:   {api_creds.api_secret}")
        print(f"Passphrase:   {api_creds.api_passphrase}")
        print("\n" + "="*60)
        print("\nSave these securely! You'll need them for the bot.")
        print("="*60)

        # Also print .env format
        print("\n\nFor your .env file, copy this:\n")
        print(f'POLYMARKET_API_KEY="{api_creds.api_key}"')
        print(f'POLYMARKET_API_SECRET="{api_creds.api_secret}"')
        print(f'POLYMARKET_PASSPHRASE="{api_creds.api_passphrase}"')
        print(f'POLYMARKET_PK="{private_key}"')
        if funder_address:
            print(f'POLYMARKET_FUNDER="{funder_address}"')

        return api_creds

    except Exception as e:
        print(f"\nError: {e}")
        print("\nIf you're using an email/Magic wallet login, you need to also")
        print("provide your funder address (your Polymarket profile address).")
        return None


if __name__ == "__main__":
    print("\n" + "="*60)
    print("POLYMARKET API KEY GENERATOR")
    print("="*60)

    # Get private key
    private_key = input("\nEnter your private key (from Polymarket export): ").strip()

    if not private_key:
        print("No private key provided!")
        sys.exit(1)

    # Ask about wallet type
    print("\nHow do you log into Polymarket?")
    print("1. Email/Magic Link")
    print("2. MetaMask/Web3 Wallet")

    choice = input("\nEnter 1 or 2: ").strip()

    funder = None
    if choice == "1":
        print("\nFor email/Magic login, you need your funder address.")
        print("This is your Polymarket profile wallet address.")
        print("Find it at: https://polymarket.com/profile (copy the address shown)")
        funder = input("\nEnter your funder address (0x...): ").strip()

    generate_credentials(private_key, funder)
