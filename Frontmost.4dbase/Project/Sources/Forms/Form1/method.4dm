Case of 
	: (Form event code:C388=On Load:K2:1)
		
		SET TIMER:C645(6)
		
	: (Form event code:C388=On Unload:K2:2)
		
		SET TIMER:C645(0)
		
	: (Form event code:C388=On Timer:K2:25)
		
		If (0=Is application frontmost)
			MAKE APPLICATION FRONTMOST
		End if 
		
End case 