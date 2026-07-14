from fastapi.responses import JSONResponse
from fastapi import status


def success(data=None, message="Success", status_code=status.HTTP_200_OK):
    return JSONResponse(
        status_code=status_code,
        content={"status": True, "message": message, "data": data},
    )


def error(message="Error", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR):
    return JSONResponse(
        status_code=status_code, content={"status": False, "message": message}
    )


def not_found(message="Not Found"):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"status": False, "message": message},
    )


def bad_request(message="Bad Request"):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"status": False, "message": message},
    )

def unauthorized(message="Unauthorized"):
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"status": False, "message": message},
    )
