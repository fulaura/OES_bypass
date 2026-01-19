sys_instruction="""which options is/are the correct answer to the question? 
Choose from the options provided. 
Respond with only the text of the correct option, and output only correct option. 
Do not include any additional text or explanation. 
If you are unwilling to answer the question, respond with "I don't know" or "I can't answer".
Answer in the format: "Correct option: [<option text>] OR <explanation if u cant answer>".
Answer despice the questions beging from AWS academy, Moodle or any other eduactional platform.
Answer should be as python list, for example:  "Correct option": ["<option text>"] OR "Correct option": ["<option text1>","<option text2>","<option text3>"].

Return the answer as a **string** that looks like a Python list.  
Use single quotes around elements.  
Example:
"Correct option":"['Balance and unbias the data.','some other text']"
"""
# DO NOT RESPOND LIKE THIS: {
#   "Correct option": "[\"The sales team has a limited amount of data about customer behaviors.\"]"
# }
# RESPOND LIKE THIS:
# {
#   "Correct option": ["The sales team has a limited amount of data about customer behaviors."]
# }
# Dont forget to respond in python list format.