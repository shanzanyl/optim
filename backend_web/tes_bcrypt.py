from app.auth import hash_password, verify_password

# Test hashing
password = "123456"
hashed = hash_password(password)
print(f"Password: {password}")
print(f"Hash: {hashed}")
print(f"Verify: {verify_password(password, hashed)}")
print(f"Verify wrong: {verify_password('wrong', hashed)}")