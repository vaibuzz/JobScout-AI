import sys
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

try:
    from pipeline.s1_ingest import ingest_profile
except ImportError as e:
    print(f"Failed to import pipeline: {e}")
    sys.exit(1)

def main():
    print("Testing Stage 1: Profile Ingestion")
    test_url = "https://www.linkedin.com/in/williamhgates/"
    print(f"Input URL: {test_url}")
    
    try:
        profile, cid = ingest_profile(linkedin_url=test_url)
        print("\nSUCCESS!")
        print(f"Candidate ID: {cid}")
        print(f"Profile Name: {profile.name}")
        print(f"Headline: {profile.headline}")
    except Exception as e:
        print(f"\nFAILED with exception:")
        print(e)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
