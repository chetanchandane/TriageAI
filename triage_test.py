import os
from dotenv import load_dotenv
from pydantic import TypeAdapter
from google import genai
from schemas import TriageResult

load_dotenv()
client = genai.Client(api_key=os.environ.get("LLM_GEMINI_API_KEY"))

def test_triage(patient_message: str):
    '''
    Test the triage function with a patient message.
    
    :param patient_message: The patient's message to be triaged.
    :type patient_message: str
    :return: TriageResult object containing the triage details.
    :rtype: TriageResult
    '''
    # Move rules to system_instruction for better steering
    PROMPT = """
    You are a professional Medical Triage Agent for a patient portal.
    Analyze the incoming message and categorize it based on these clinical rules:
    - EMERGENCY: Life-threatening (Chest pain, difficulty breathing, severe bleeding).
    - HIGH: Acute pain or worsening symptoms (needs attention < 24 hrs).
    - NORMAL: Standard clinical questions, follow-ups.
    - LOW: Administrative (refills, scheduling).
    
    Ensure 'confidence' is a float between 0 and 1.
    """

    # We use 'response.parsed' to get the Pydantic object directly
    response = client.models.generate_content(
        model="gemini-2.5-flash", 
        contents=patient_message,
        config={
            "system_instruction": PROMPT,
            "response_mime_type": "application/json",
            "response_schema": TriageResult, 
        }
    )

    # Gemini handles the validation for you here
    return response.parsed

# Test cases
test_cases = [
    "I have been having sharp chest pain for the last 10 minutes and it is spreading to my arm.",
    "Hi, I just need a refill for my Lisinopril prescription."
]

print(f"--- Testing Emergency Case ---")
output = test_triage(test_cases[0])
print(output.model_dump_json(indent=2))

print(f"\n--- Testing Administrative Case ---")
output_low = test_triage(test_cases[1])
print(output_low.model_dump_json(indent=2))

# for msg in test_cases:
#     print(f"\n--- Testing Message: {msg} ---")
#     output = test_triage(msg)
#     print(output.model_dump_json(indent=2))