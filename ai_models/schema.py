from google import genai

resp_schema= {
    "list_obj":genai.types.Schema(
            type = genai.types.Type.OBJECT,
            required = ["Correct option"],
            properties = {
                "Correct option": genai.types.Schema(
                    type = genai.types.Type.ARRAY,
                    items = genai.types.Schema(
                        type = genai.types.Type.STRING,
                    ),
                ),
            },
        ),
    "strlist_obj":genai.types.Schema(
			type=genai.types.Type.OBJECT,
			properties={
				"Correct option": genai.types.Schema(
					type=genai.types.Type.STRING,
				),
			},
		),
    
}



