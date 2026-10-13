import requests

url = "http://localhost:8000/pseudorun"
files = {"file": ("1.pdf", open("1.pdf", "rb"), "application/pdf")}

print("Sending request to", url)
response = requests.post(url, files=files)
print("Status Code:", response.status_code)
if response.status_code == 200:
    res = response.json()
    print("Keys in response:", res.keys())
    print("\nSnippet of sanitized text:")
    print(res["sanitized_text"][:500])
else:
    print(response.text)
